"""관절 26점(RTMPose-m Halpe26). 설계: design/2026-09-30-body-detail-design.md 6.1·6.3·6.5

YOLO가 찾은 사람 상자마다 잘라 26점 모델을 돌린다. 전처리·복원은 rtmlib 0.0.16
(https://github.com/Tau-J/rtmlib, Apache-2.0)의 RTMPose 구현을 참고해 다시 썼다.
rtmlib과 다른 점: 입력을 RGB로 넣는다(mmpose 학습 설정 bgr_to_rgb=True), 여러 사람을 묶어 한 번에 추론한다,
가장자리 칸·이미지 밖 점을 0점으로 정리한다(서버 고유 규칙).

onnxruntime 호출은 스레드 안전하게 쓰지 않는다. app.py의 단일 실행기에서만 부른다.
"""
import contextlib
import hashlib
import io
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
             "rtmpose-m_simcc-body7_pt-body7-halpe26_700e-256x192-4d3e73dd_20230605.zip")
MODEL_SHA256 = "26f3a19e61304a600dfb82d1001d41d24343b89fc70a33ffc84657e0b0bf2ecf"
MODEL_PATH = Path(__file__).resolve().parent / "models" / "rtmpose-m_halpe26.onnx"

INPUT_W, INPUT_H = 192, 256
PADDING = 1.25
SPLIT = 2.0  # SimCC 분할 비율: 칸 번호 ÷ 2 = 입력 픽셀
MEAN = (123.675, 116.28, 103.53)  # RGB
STD = (58.395, 57.12, 57.375)
BATCH = 4  # GPU에서는 늘 이 크기로 채운다 (크기가 바뀔 때마다 60ms 넘게 멈춤, 설계 4장)
NUM_KPTS = 26
EDGE_BINS = 2  # 첫·끝 칸에서 이만큼 안쪽까지를 "가장자리에 붙은 점"으로 본다 (0·1, 끝·끝-1)
FIX_HINT = r"해결: .venv\Scripts\python tools\fetch_models.py 실행 후 서버 재시작"


def box_center_scale(box):
    """xyxy 상자 → 중심, 1.25배 넓히고 192:256 가로세로비에 맞춘 크기."""
    x1, y1, x2, y2 = (float(v) for v in box)
    center = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
    w, h = (x2 - x1) * PADDING, (y2 - y1) * PADDING
    aspect = INPUT_W / INPUT_H
    scale = np.array([w, w / aspect]) if w > h * aspect else np.array([h * aspect, h])
    return center, scale


def warp_matrix(center, scale, inverse=False):
    """상자 영역 → 192x256 입력 (회전 없음이라 확대·이동뿐). inverse=True면 반대 방향."""
    k = INPUT_W / scale[0]
    if inverse:
        return np.array([[1 / k, 0, center[0] - INPUT_W / 2 / k], [0, 1 / k, center[1] - INPUT_H / 2 / k]])
    return np.array([[k, 0, INPUT_W / 2 - k * center[0]], [0, k, INPUT_H / 2 - k * center[1]]])


def crop(img_rgb, box):
    """RGB 이미지와 상자 → (3, 256, 192) float32 입력, 중심, 크기."""
    center, scale = box_center_scale(box)
    patch = cv2.warpAffine(img_rgb, warp_matrix(center, scale), (INPUT_W, INPUT_H), flags=cv2.INTER_LINEAR)
    x = (patch.astype(np.float32) - np.array(MEAN, np.float32)) / np.array(STD, np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1)), center, scale


def restore(simcc_x, simcc_y, centers, scales):
    """모델 출력 → 원본 픽셀 좌표 (N, K, 2), 원시 점수 (N, K), 최댓값 칸 (N, K, 2). rtmlib 후처리와 같은 계산 순서."""
    lx, ly = simcc_x.argmax(-1), simcc_y.argmax(-1)
    locs = np.stack([lx, ly], -1).astype(np.float32)
    scores = (simcc_x.max(-1) + simcc_y.max(-1)) / 2
    locs[scores <= 0] = -1
    kpts = locs / SPLIT
    kpts = kpts / np.array([INPUT_W, INPUT_H]) * scales[:, None, :]
    kpts = kpts + centers[:, None, :] - scales[:, None, :] / 2
    return kpts, scores, locs.astype(np.int64)


def clean(kpts, scores, locs, img_w, img_h):
    """서버 고유 정리 (설계 6.1): 가장자리 칸 0점 → 이미지 밖 0점 → 0~1로 자르고 비율 좌표로."""
    nx, ny = INPUT_W * SPLIT, INPUT_H * SPLIT
    lx, ly = locs[..., 0], locs[..., 1]
    edge = (lx < EDGE_BINS) | (lx >= nx - EDGE_BINS) | (ly < EDGE_BINS) | (ly >= ny - EDGE_BINS)
    x, y = kpts[..., 0], kpts[..., 1]
    outside = (x < 0) | (x >= img_w) | (y < 0) | (y >= img_h)
    s = np.where(edge | outside, 0.0, np.clip(scores, 0.0, 1.0))
    nxs, nys = np.clip(x / img_w, 0, 1), np.clip(y / img_h, 0, 1)
    return [[[round(float(a), 4), round(float(b), 4), round(float(c), 3)] for a, b, c in zip(px, py, pc)]
            for px, py, pc in zip(nxs, nys, s)]


class DetailModel:
    def __init__(self, session):
        self.session = session
        self.gpu = session.get_providers()[0] == "CUDAExecutionProvider"
        self.input_name = session.get_inputs()[0].name

    def _infer(self, batch):
        n = len(batch)
        if self.gpu and n < BATCH:
            batch = np.concatenate([batch, np.zeros((BATCH - n, 3, INPUT_H, INPUT_W), np.float32)])
        sx, sy = self.session.run(None, {self.input_name: batch})
        return sx[:n], sy[:n]

    def warmup(self):
        self._infer(np.zeros((BATCH if self.gpu else 1, 3, INPUT_H, INPUT_W), np.float32))

    def raw(self, img_rgb, boxes):
        """복원 단계까지 (원본 픽셀 좌표, 원시 점수, 칸) — 원본 대조 테스트용."""
        crops = [crop(img_rgb, b) for b in boxes]
        kpts, scores, locs = [], [], []
        for i in range(0, len(crops), BATCH):
            part = crops[i:i + BATCH]
            sx, sy = self._infer(np.stack([c[0] for c in part]))
            k, s, l = restore(sx, sy, np.stack([c[1] for c in part]), np.stack([c[2] for c in part]))
            kpts.append(k), scores.append(s), locs.append(l)
        return np.concatenate(kpts), np.concatenate(scores), np.concatenate(locs)

    def __call__(self, img_bgr, boxes):
        """BGR 이미지(decode_jpeg 결과)와 픽셀 상자들 → 사람마다 [[x, y, c] × 26] (0~1 비율)."""
        if len(boxes) == 0:
            return []
        rgb = np.ascontiguousarray(img_bgr[:, :, ::-1])
        kpts, scores, locs = self.raw(rgb, boxes)
        return clean(kpts, scores, locs, img_bgr.shape[1], img_bgr.shape[0])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _ort_session(path: Path):
    import onnxruntime as ort
    # torch(cu12x)의 lib 폴더에서 CUDA·cuDNN DLL을 불러온다. 순서에 기대지 않고 명시적으로 (설계 6.3)
    with contextlib.redirect_stdout(io.StringIO()):
        ort.preload_dlls()
    opts = ort.SessionOptions()
    opts.log_severity_level = 3  # 정상 동작에서도 뜨는 노드 배치 경고를 끈다 (GPU 실패로 오해하지 않게, 설계 6.5)
    return ort.InferenceSession(str(path), opts, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])


def load(path: Path = MODEL_PATH, sha256: str = MODEL_SHA256, make_session=_ort_session):
    """(DetailModel 또는 None, 서버 창에 띄울 상태 문구). 어떤 실패에도 예외를 내지 않는다 (서버는 17점으로 계속)."""
    path = Path(path)
    if not path.exists():
        return None, f"모델 파일이 없습니다 ({path.name}). {FIX_HINT}"
    if _sha256(path) != sha256:
        return None, f"모델 파일 해시가 다릅니다 ({path.name}). {FIX_HINT}"
    try:
        session = make_session(path)
    except Exception as exc:  # onnxruntime 불러오기 실패, 모델 손상, 공급자 초기화 오류
        return None, f"세션을 만들지 못했습니다 ({type(exc).__name__}). {FIX_HINT}"
    model = DetailModel(session)
    try:
        model.warmup()
    except Exception as exc:  # CUDA·cuDNN 오류 등
        return None, f"예열 실행에 실패했습니다 ({type(exc).__name__})"
    if model.gpu:
        return model, "관절 26점 (RTMPose-m, GPU)"
    return model, ("관절 26점 (RTMPose-m, CPU) — 경고: GPU를 잡지 못했습니다. 사람이 여럿이면 30ms 목표를 넘을 수 있습니다 "
                   "(torch와 onnxruntime-gpu의 CUDA 판이 맞는지 README 확인)")
