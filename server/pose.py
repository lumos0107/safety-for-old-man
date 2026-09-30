"""YOLO11n-pose 래퍼 (+ 관절 26점). 결과를 JSON으로 보낼 수 있는 0~1 비율 좌표로 바꾼다.

ultralytics 모델과 onnxruntime 세션은 스레드 안전하게 쓰지 않는다. 호출은 app.py의 단일 실행기에서만 한다.
관절 26점: design/2026-09-30-body-detail-design.md — YOLO가 찾은 상자마다 RTMPose(server/pose_detail.py)를 돌린다.
"""
import logging
import time

import numpy as np
from ultralytics import YOLO
from ultralytics.utils.events import events

# Ultralytics는 추론 통계(PC 식별자·GPU 이름·처리 장수 등)를 Google Analytics로 보낸다.
# 화면 쪽 MediaPipe 통계를 CSP로 막은 것과 같은 원칙으로, 이 서버 프로세스에서는 끈다 (전역 설정은 건드리지 않음).
events.enabled = False

log = logging.getLogger("pose")
DETAIL_MAX_FAILURES = 5  # 26점이 연속으로 이만큼 실패하면 끄고 17점으로 계속한다 (설계 6.5)


def _unit(v: float) -> float:
    return round(min(max(float(v), 0.0), 1.0), 4)


def _note(message: str) -> None:
    log.warning("%s %s", time.strftime("%H:%M:%S"), message)


class PoseModel:
    def __init__(self, weights: str = "yolo11n-pose.pt", imgsz: int = 640, conf: float = 0.5, detail=None):
        self.model = YOLO(weights)  # 가중치가 없으면 첫 실행 때 자동으로 내려받는다
        self.imgsz = imgsz
        self.conf = conf
        self.detail = detail  # server.pose_detail.DetailModel 또는 None(17점)
        self.detail_failures = 0
        self.predict(np.zeros((480, 640, 3), np.uint8))  # 예열

    def predict(self, img_bgr: np.ndarray) -> dict:
        start = time.perf_counter()  # infer_ms = 이 함수 전체의 벽시계 시간 (JPEG 디코드 제외, 설계 6.2)
        r = self.model.predict(img_bgr, imgsz=self.imgsz, conf=self.conf, classes=[0],
                               verbose=False, save=False)[0]
        h, w = r.orig_shape
        people, pixel_boxes = [], []
        if r.boxes is not None and len(r.boxes) and r.keypoints is not None:
            boxes = r.boxes.xyxyn.cpu().numpy()
            pixel_boxes = r.boxes.xyxy.cpu().numpy().tolist()
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
        layout = "coco17"
        if self.detail is not None:
            layout = "halpe26"
            if people:
                layout = self._apply_detail(img_bgr, pixel_boxes, people)
        return {"img_w": int(w), "img_h": int(h), "infer_ms": round((time.perf_counter() - start) * 1000, 1),
                "layout": layout, "people": people}

    def _apply_detail(self, img_bgr, pixel_boxes, people) -> str:
        """26점으로 바꾼다. 실패하면 이 프레임은 이미 계산한 17점을 그대로 보낸다 (server_error로 버리지 않음)."""
        try:
            detailed = self.detail(img_bgr, pixel_boxes)
        except Exception as exc:  # GPU 메모리 부족 등
            self.detail_failures += 1
            _note(f"관절 26점 계산 실패 ({type(exc).__name__}) — 이 프레임은 17점으로 보냄 "
                  f"(연속 {self.detail_failures}/{DETAIL_MAX_FAILURES})")
            if self.detail_failures >= DETAIL_MAX_FAILURES:
                self.detail = None
                _note("관절 26점이 연속으로 실패해 끕니다 — 17점으로 계속 (다시 켜려면 서버 재시작)")
            return "coco17"
        self.detail_failures = 0
        for p, k in zip(people, detailed):
            p["kpts"] = k
        return "halpe26"
