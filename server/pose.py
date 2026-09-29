"""YOLO11n-pose 래퍼. 결과를 JSON으로 보낼 수 있는 0~1 비율 좌표로 바꾼다.

ultralytics 모델은 스레드 안전하지 않다. 호출은 app.py의 단일 실행기에서만 한다.
"""
import numpy as np
from ultralytics import YOLO
from ultralytics.utils.events import events

# Ultralytics는 추론 통계(PC 식별자·GPU 이름·처리 장수 등)를 Google Analytics로 보낸다.
# 화면 쪽 MediaPipe 통계를 CSP로 막은 것과 같은 원칙으로, 이 서버 프로세스에서는 끈다 (전역 설정은 건드리지 않음).
events.enabled = False


def _unit(v: float) -> float:
    return round(min(max(float(v), 0.0), 1.0), 4)


class PoseModel:
    def __init__(self, weights: str = "yolo11n-pose.pt", imgsz: int = 640, conf: float = 0.5):
        self.model = YOLO(weights)  # 가중치가 없으면 첫 실행 때 자동으로 내려받는다
        self.imgsz = imgsz
        self.conf = conf
        self.predict(np.zeros((480, 640, 3), np.uint8))  # 예열

    def predict(self, img_bgr: np.ndarray) -> dict:
        r = self.model.predict(img_bgr, imgsz=self.imgsz, conf=self.conf, classes=[0],
                               verbose=False, save=False)[0]
        h, w = r.orig_shape
        people = []
        if r.boxes is not None and len(r.boxes) and r.keypoints is not None:
            boxes = r.boxes.xyxyn.cpu().numpy()
            scores = r.boxes.conf.cpu().numpy()
            kxy = r.keypoints.xyn.cpu().numpy()
            kconf = (r.keypoints.conf.cpu().numpy() if r.keypoints.conf is not None
                     else np.ones(kxy.shape[:2], np.float32))
            for box, score, pts, confs in zip(boxes, scores, kxy, kconf):
                people.append({
                    "box": [_unit(v) for v in box],
                    "score": round(float(score), 3),
                    "kpts": [[_unit(x), _unit(y), round(float(c), 3)] for (x, y), c in zip(pts, confs)],
                })
        return {"img_w": int(w), "img_h": int(h),
                "infer_ms": round(float(r.speed["inference"]), 1), "people": people}
