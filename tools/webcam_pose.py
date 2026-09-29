"""웹캠 실시간 사람 검출 + 관절 키포인트(YOLO-Pose) 시험 스크립트.

실행:  .venv\Scripts\python webcam_pose.py [--cam 0] [--model yolo11n-pose.pt]
종료:  q 또는 ESC
영상은 저장하지 않는다 (화면 표시만).
"""
import argparse
import time

import cv2
from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0, help="웹캠 번호")
    ap.add_argument("--model", default="yolo11n-pose.pt", help="포즈 모델 (n/s/m/l/x)")
    ap.add_argument("--conf", type=float, default=0.5, help="사람 검출 신뢰도 하한")
    args = ap.parse_args()

    model = YOLO(args.model)
    cap = cv2.VideoCapture(args.cam, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit(f"웹캠 {args.cam}번을 열 수 없습니다.")

    fps, prev = 0.0, time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        result = model(frame, conf=args.conf, classes=[0], verbose=False)[0]
        view = result.plot()  # 사람 박스 + 17개 관절 뼈대

        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 * (1.0 / (now - prev)) if fps else 1.0 / (now - prev)
        prev = now
        infer_ms = result.speed["inference"]
        cv2.putText(view, f"FPS {fps:5.1f}  infer {infer_ms:4.1f}ms  people {len(result.boxes)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        cv2.imshow("webcam pose (q: quit)", view)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
