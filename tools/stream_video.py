"""동영상(없으면 예시 사진 반복)을 /ws로 흘려보내 카메라 없이 전체 경로를 검증한다.

사용:
  .venv\\Scripts\\python tools/stream_video.py --url ws://127.0.0.1:8000/ws
  .venv\\Scripts\\python tools/stream_video.py --url wss://<pc이름>.<tailnet>.ts.net/ws --video 파일.mp4
토큰은 server/.env의 TOKEN을 쓴다 (--token으로 바꿀 수 있음).
"""
import argparse
import json
import time
from pathlib import Path

import cv2
from dotenv import dotenv_values
from ultralytics.utils import ASSETS
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]


def frames(video: str | None, n: int):
    if video is None:
        img = cv2.imread(str(ASSETS / "bus.jpg"))
        for _ in range(n):
            yield img
        return
    cap = cv2.VideoCapture(video)
    try:
        for _ in range(n):
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def to_jpeg(img, max_side: int = 640, quality: int = 70) -> bytes:
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="ws://…/ws 또는 wss://…/ws")
    ap.add_argument("--video", help="동영상 파일 (없으면 예시 사진 반복)")
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--token")
    ap.add_argument("--origin", help="Origin 헤더 (예: https://lumos0107.github.io)")
    args = ap.parse_args()

    token = args.token or dotenv_values(ROOT / "server" / ".env").get("TOKEN")
    if not token:
        raise SystemExit("토큰이 없습니다: server/.env 또는 --token")

    done = errors = people = 0
    infer = 0.0
    try:
        with connect(args.url, origin=args.origin, open_timeout=20) as ws:
            ws.send(json.dumps({"type": "auth", "token": token.strip()}))
            ready = json.loads(ws.recv(timeout=10))
            start = time.perf_counter()
            for seq, img in enumerate(frames(args.video, args.frames)):
                ws.send(json.dumps({"type": "frame", "seq": seq}))
                ws.send(to_jpeg(img))
                r = json.loads(ws.recv(timeout=10))
                if r.get("seq") != seq:
                    raise SystemExit(f"seq 불일치: 보냄 {seq}, 받음 {r}")
                if r["type"] == "result":
                    done += 1
                    infer += r["infer_ms"]
                    people += len(r["people"])
                else:
                    errors += 1
            elapsed = time.perf_counter() - start
    except ConnectionClosed as exc:
        code = exc.rcvd.code if exc.rcvd else "-"
        raise SystemExit(f"서버가 연결을 닫음 (코드 {code})")
    if done == 0:
        raise SystemExit(f"결과 0건 (오류 {errors})")
    print(f"모델 {ready['model']}  프레임 {done}  오류 {errors}  왕복 fps {done / elapsed:.1f}  "
          f"평균 추론 {infer / done:.1f}ms  평균 인원 {people / done:.2f}")


if __name__ == "__main__":
    main()
