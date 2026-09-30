"""관절 세분화 설계(design/2026-09-30-body-detail-design.md) 4장의 실험을 한 번에 다시 재는 스크립트.

설계 결정의 근거 수치를 다시 만들 수 있게 남긴다. 서버 코드가 아니며 테스트에서 돌지 않는다.
실험 당시 환경: .venv + onnxruntime-gpu 1.23.2 + rtmlib 0.0.16(--no-deps 설치, 서버는 rtmlib을 쓰지 않음).
결과 이미지는 OUT 폴더(저장소 밖 기본값)에 저장한다 — 저장소는 사진을 올리지 않는다(.gitignore).

사용: .venv\\Scripts\\python design/eval/body_detail/spike.py [OUT 폴더] > design/eval/body_detail/spike.log
"""
import sys
import tempfile
import time
from pathlib import Path

import torch  # noqa: F401  (onnxruntime-gpu가 torch의 CUDA DLL을 찾게 먼저 불러온다)
import cv2
import numpy as np
import onnxruntime as ort
from rtmlib import RTMPose, YOLOX, draw_skeleton
from ultralytics import YOLO

ASSETS = Path(__file__).resolve().parents[3] / ".venv/Lib/site-packages/ultralytics/assets"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "body_detail_spike"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://download.openmmlab.com/mmpose/v1/projects/"
POSE = {
    "halpe26-s": BASE + "rtmposev1/onnx_sdk/rtmpose-s_simcc-body7_pt-body7-halpe26_700e-256x192-7f134165_20230605.zip",
    "halpe26-m": BASE + "rtmposev1/onnx_sdk/rtmpose-m_simcc-body7_pt-body7-halpe26_700e-256x192-4d3e73dd_20230605.zip",
    "wholebody-rtmw-m": BASE + "rtmw/onnx_sdk/rtmw-dw-l-m_simcc-cocktail14_270e-256x192_20231122.zip",
}
YOLOX_M = BASE + "rtmposev1/onnx_sdk/yolox_m_8xb8-300e_humanart-c2c7a14a.zip"
FEET = slice(20, 26)


def ms(fn, n=30):
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1000


def on_canvas(p, rot):
    """사람 사진을 돌려 1280x720 회색 바탕 가운데 놓는다. 정답 상자도 돌려준다."""
    if rot is not None:
        p = cv2.rotate(p, rot)
    s = min(600 / p.shape[0], 1200 / p.shape[1])
    p = cv2.resize(p, None, fx=s, fy=s)
    c = np.full((720, 1280, 3), 120, np.uint8)
    oy, ox = (720 - p.shape[0]) // 2, (1280 - p.shape[1]) // 2
    c[oy:oy + p.shape[0], ox:ox + p.shape[1]] = p
    return c, (ox, oy, ox + p.shape[1], oy + p.shape[0])


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def main():
    print("onnxruntime", ort.__version__)
    det = YOLO("yolo11n-pose.pt")
    bus = cv2.imread(str(ASSETS / "bus.jpg"))
    zid = cv2.imread(str(ASSETS / "zidane.jpg"))
    boxes = lambda img, conf=0.5: det.predict(img, conf=conf, classes=[0], verbose=False)[0].boxes.xyxy.cpu().numpy().tolist()

    print("\n== 1. 지금 서버 기준값: YOLO11n-pose predict() 벽시계 시간 (640 긴 변) ==")
    for name, img in [("bus", bus), ("zidane", zid)]:
        small = cv2.resize(img, None, fx=640 / max(img.shape[:2]), fy=640 / max(img.shape[:2]))
        print(f"{name}: {ms(lambda: det.predict(small, imgsz=640, conf=0.5, classes=[0], verbose=False)):.1f}ms")

    print("\n== 2. 26점/133점 모델 속도 (rtmlib, 사람마다 batch=1) ==")
    models = {k: RTMPose(u, model_input_size=(192, 256), backend="onnxruntime", device="cuda") for k, u in POSE.items()}
    bb = boxes(bus)  # 상자는 미리 구해 둔다 (YOLO 시간이 섞이지 않게)
    for k, m in models.items():
        print(f"{k}: provider={m.session.get_providers()[0]} 1명 {ms(lambda: m(bus, bb[:1])):.1f}ms, "
              f"3명 {ms(lambda: m(bus, bb[:3])):.1f}ms")
    cpu = RTMPose(POSE["halpe26-m"], model_input_size=(192, 256), backend="onnxruntime", device="cpu")
    print(f"halpe26-m CPU: 1명 {ms(lambda: cpu(bus, bb[:1]), 10):.1f}ms, 3명 {ms(lambda: cpu(bus, bb[:3]), 10):.1f}ms")

    print("\n== 3. 묶음(batch) 크기가 바뀔 때 (halpe26-m 세션 직접, GPU) ==")
    s = models["halpe26-m"].session
    inputs = {b: np.random.rand(b, 3, 256, 192).astype(np.float32) for b in (1, 2, 3, 4)}  # 입력은 미리 만든다
    run = lambda b: s.run(None, {"input": inputs[b]})
    seq = [1, 2, 1, 2, 3, 1, 3, 2, 1, 1, 2, 2]
    for b in (1, 2, 3, 4):
        run(b)
    out = []
    for b in seq:
        t = time.perf_counter(); run(b); out.append(f"{b}명:{(time.perf_counter() - t) * 1000:.0f}")
    print("가변 batch:", " ".join(out))
    out = []
    for b in seq:
        t = time.perf_counter(); run(4); out.append(f"{b}명:{(time.perf_counter() - t) * 1000:.1f}")
    print("4로 고정해 채움:", " ".join(out))

    print("\n== 4. 서 있는 사람 점수 (halpe26-m) ==")
    m = models["halpe26-m"]
    for name, img in [("bus", bus), ("zidane", zid)]:
        kp, sc = m(img, boxes(img))
        print(f"{name}: 점수 범위 {sc.min():.2f}~{sc.max():.2f}, 발 6점 점수 {np.round(sc[:, FEET], 2).tolist()}")
        h, w = img.shape[:2]
        outside = [(i, j, round(float(sc[i, j]), 2)) for i in range(len(kp)) for j in range(26)
                   if not (0 <= kp[i, j, 0] < w and 0 <= kp[i, j, 1] < h)]
        print(f"  이미지 밖 점 (사람, 번호, 점수): {outside}")
        cv2.imwrite(str(OUT / f"upright_{name}.jpg"), draw_skeleton(img.copy(), kp, sc, kpt_thr=0.4))

    print("\n== 5. 누운 자세 흉내 (사람을 잘라 돌림) ==")
    x1, y1, x2, y2 = map(int, boxes(bus)[0])
    bus_person = bus[max(0, y1 - 20):y2 + 20, max(0, x1 - 20):x2 + 20]
    zid_person = zid[0:720, 640:1280]  # 하반신이 잘린 상반신 사진
    cases = [("bus 서 있음", bus_person, None), ("bus 90", bus_person, cv2.ROTATE_90_CLOCKWISE),
             ("bus 270", bus_person, cv2.ROTATE_90_COUNTERCLOCKWISE),
             ("zidane 90", zid_person, cv2.ROTATE_90_CLOCKWISE), ("zidane 270", zid_person, cv2.ROTATE_90_COUNTERCLOCKWISE)]
    scenes = {}
    for name, person, rot in cases:
        c, gt = on_canvas(person, rot)
        scenes[name] = (c, gt)
        r = det.predict(c, conf=0.3, classes=[0], verbose=False)[0]
        if r.boxes is None or not len(r.boxes):
            print(f"{name}: YOLO가 사람을 못 찾음")
            continue
        b = r.boxes.xyxy.cpu().numpy().tolist()
        best = max(range(len(b)), key=lambda i: iou(b[i], gt))
        kp, sc = m(c, [b[best]])
        print(f"{name}: 상자 {[int(v) for v in b[best]]} (IoU {iou(b[best], gt):.2f}), "
              f"YOLO 17점 점수 {np.round(r.keypoints.conf[best].cpu().numpy(), 2).tolist()}")
        print(f"  RTMPose 26점 점수 {np.round(sc[0], 2).tolist()}")
        print(f"  RTMPose 26점 좌표 {[[int(x), int(y)] for x, y in kp[0]]}")
        tag = name.replace(" ", "_")
        cv2.imwrite(str(OUT / f"lying_{tag}_yolo.jpg"), r.plot())
        cv2.imwrite(str(OUT / f"lying_{tag}_rtm.jpg"), draw_skeleton(c.copy(), kp, sc, kpt_thr=0.4))

    print("\n== 6. 사람 찾기(검출기) 비교: 정답 상자와의 최대 IoU ==")
    dets = {n: YOLO(n) for n in ["yolo11n-pose.pt", "yolo11m-pose.pt", "yolo11n.pt", "yolo11m.pt", "yolo11x.pt"]}
    yx = YOLOX(YOLOX_M, model_input_size=(640, 640), backend="onnxruntime", device="cuda")
    for n, d in dets.items():
        row = []
        for name in ("bus 서 있음", "bus 90", "bus 270", "zidane 90"):
            c, gt = scenes[name]
            r = d.predict(c, conf=0.3, classes=[0], verbose=False)[0]
            row.append(f"{name}:{max([iou(b, gt) for b in r.boxes.xyxy.tolist()], default=0):.2f}")
        print(n, " ".join(row))
    row = []
    for name in ("bus 서 있음", "bus 90", "bus 270", "zidane 90"):
        c, gt = scenes[name]
        row.append(f"{name}:{max([iou(b, gt) for b in yx(c).tolist()], default=0):.2f}")
    print("yolox-m-humanart", " ".join(row))
    print("\n이미지 저장:", OUT)


if __name__ == "__main__":
    main()
