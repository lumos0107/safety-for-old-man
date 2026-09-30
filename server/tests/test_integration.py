"""실제 uvicorn을 띄워 TestClient가 거치지 않는 부분(--ws-max-size, 실제 모델)을 검증한다."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from ultralytics.utils import ASSETS
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from server.pose_detail import MODEL_PATH

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "i" * 43


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    env = {**os.environ, "TOKEN": TOKEN, "ALLOWED_ORIGINS": "https://lumos0107.github.io"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "server.app:build_app", "--host", "127.0.0.1",
         "--port", str(port), "--ws-max-size", "2097152", "--log-level", "warning"],
        cwd=ROOT, env=env)
    base = f"127.0.0.1:{port}"
    deadline = time.time() + 120
    while True:
        if proc.poll() is not None:
            pytest.fail(f"서버가 종료됨 (코드 {proc.returncode})")
        try:
            urllib.request.urlopen(f"http://{base}/health", timeout=1)
            break
        except (urllib.error.URLError, ConnectionError):
            if time.time() > deadline:
                proc.kill()
                pytest.fail("서버가 120초 안에 뜨지 않음")
            time.sleep(0.5)
    yield base
    proc.terminate()
    proc.wait(timeout=15)


@contextmanager
def authed(base):
    with connect(f"ws://{base}/ws", open_timeout=10) as ws:
        ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        assert json.loads(ws.recv(timeout=10)) == {"type": "ready", "model": "yolo11n-pose"}
        yield ws


def test_health_and_docs_hidden(server):
    assert json.loads(urllib.request.urlopen(f"http://{server}/health").read()) == {"ok": True}
    for path in ("/docs", "/redoc", "/openapi.json"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://{server}{path}")
        assert exc.value.code == 404


def test_bus_end_to_end(server):
    with authed(server) as ws:
        ws.send(json.dumps({"type": "frame", "seq": 42}))
        ws.send((ASSETS / "bus.jpg").read_bytes())
        r = json.loads(ws.recv(timeout=30))
    assert r["type"] == "result" and r["seq"] == 42
    assert (r["img_w"], r["img_h"]) == (810, 1080)
    assert len(r["people"]) >= 3
    # 모델 파일이 있으면 관절 26점, 없으면 대비 동작으로 17점 (설계 6.5)
    expected = ("halpe26", 26) if MODEL_PATH.exists() else ("coco17", 17)
    assert r["layout"] == expected[0]
    for p in r["people"]:
        assert len(p["kpts"]) == expected[1]
        assert all(0 <= v <= 1 for x, y, _ in p["kpts"] for v in (x, y))


def test_over_ws_max_size_disconnects(server):
    with authed(server) as ws:
        ws.send(json.dumps({"type": "frame", "seq": 1}))
        # 서버는 헤더만 보고 바로 닫으므로 send에서 먼저 끊김(Windows는 RST)이 날 수 있다
        with pytest.raises((ConnectionClosed, OSError)):
            ws.send(b"\xff" * 2_200_000)
            ws.recv(timeout=10)
