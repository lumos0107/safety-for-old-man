"""원본 대조 테스트(설계 6.7)의 기준값을 rtmlib으로 만든다. 서버·테스트는 이 파일을 불러오지 않는다.

rtmlib은 서버 의존성이 아니다. 실행 전 임시로 설치하고 끝나면 지운다 (requirements·lock에 넣지 않음):
    uv pip install --python .venv rtmlib==0.0.16 --no-deps
    .venv\\Scripts\\python design/eval/body_detail/make_reference.py
    uv pip uninstall --python .venv rtmlib

산출물 server/tests/data/rtmpose_ref.npz
- pre_*  : bus 0번 사람 — 전처리 결과(HWC, 정규화 뒤)의 채널별 평균·표준편차와 정해 둔 위치의 값, 중심·크기
- post_* : 같은 사람 — 모델 출력(simcc_x, simcc_y)과 rtmlib 복원 결과(픽셀 좌표·원시 점수)
- e2e_<이미지>_* : 사람 상자(YOLO11n-pose, conf 0.5)와 rtmlib 결과 (GPU, 사람마다 batch 1)
입력은 모두 server.imaging.decode_jpeg(BGR)를 RGB로 뒤집은 배열이다 (설계 6.1 — rtmlib은 색을 바꾸지 않는다).
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import onnxruntime as ort  # noqa: E402
from rtmlib import RTMPose  # noqa: E402
from ultralytics import YOLO  # noqa: E402
from ultralytics.utils import ASSETS  # noqa: E402

from server.imaging import decode_jpeg  # noqa: E402

MODEL = ROOT / "server/models/rtmpose-m_halpe26.onnx"
OUT = ROOT / "server/tests/data/rtmpose_ref.npz"
SAMPLES = 256


def main():
    ort.preload_dlls()
    m = RTMPose(str(MODEL), model_input_size=(192, 256), backend="onnxruntime", device="cuda")
    assert m.session.get_providers()[0] == "CUDAExecutionProvider", "GPU를 잡지 못함"
    det = YOLO(str(ROOT / "yolo11n-pose.pt"))
    out = {}
    for name in ("bus", "zidane"):
        bgr = decode_jpeg((ASSETS / f"{name}.jpg").read_bytes())
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        boxes = det.predict(bgr, conf=0.5, classes=[0], verbose=False)[0].boxes.xyxy.cpu().numpy().astype(np.float64)
        kpts, scores = m(rgb, boxes.tolist())
        out[f"e2e_{name}_boxes"] = boxes
        out[f"e2e_{name}_kpts"] = kpts
        out[f"e2e_{name}_scores"] = scores
        if name == "bus":
            box = boxes[0].tolist()
            img, center, scale = m.preprocess(rgb, box)
            rng = np.random.default_rng(0)
            idx = np.stack([rng.integers(0, 256, SAMPLES), rng.integers(0, 192, SAMPLES), rng.integers(0, 3, SAMPLES)], 1)
            out["pre_box"] = np.array(box)
            out["pre_center"], out["pre_scale"] = center, scale
            out["pre_mean"], out["pre_std"] = img.mean((0, 1)), img.std((0, 1))
            out["pre_idx"], out["pre_vals"] = idx, img[idx[:, 0], idx[:, 1], idx[:, 2]]
            sx, sy = m.inference(img)
            k, s = m.postprocess([sx, sy], center, scale)
            out["post_simcc_x"], out["post_simcc_y"] = sx, sy
            out["post_center"], out["post_scale"] = center, scale
            out["post_kpts"], out["post_scores"] = k, s
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, **out)
    print(f"{OUT} 저장 ({OUT.stat().st_size // 1024}KB): " + ", ".join(f"{k}{list(v.shape)}" for k, v in out.items()))


if __name__ == "__main__":
    main()
