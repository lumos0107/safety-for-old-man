import threading
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from server.tests.conftest import TOKEN, authed, fake_predictor
from server.tests.helpers import jpeg_bytes, jpeg_with_fake_size

JPEG = jpeg_bytes(64, 48)
BAD_MESSAGE = {"type": "error", "code": "bad_message"}


def send_frame(ws, seq, data=JPEG):
    ws.send_json({"type": "frame", "seq": seq})
    ws.send_bytes(data)
    return ws.receive_json()


def test_result_echoes_seq_and_size(make_client):
    with make_client() as client, authed(client) as ws:
        assert send_frame(ws, 7) == {"type": "result", "seq": 7, "img_w": 64, "img_h": 48,
                                     "infer_ms": 1.0, "people": []}


def test_too_large_keeps_connection(make_client):
    with make_client() as client, authed(client) as ws:
        assert send_frame(ws, 1, b"\xff" * 1_048_577) == {"type": "error", "code": "too_large", "seq": 1}
        assert send_frame(ws, 2)["type"] == "result"


@pytest.mark.parametrize("data", [b"garbage", jpeg_with_fake_size(30000, 30000), jpeg_bytes(2001, 8)])
def test_bad_image_keeps_connection(make_client, data):
    with make_client() as client, authed(client) as ws:
        assert send_frame(ws, 3, data) == {"type": "error", "code": "bad_image", "seq": 3}
        assert send_frame(ws, 4)["seq"] == 4


@pytest.mark.parametrize("message", [
    "hello",
    '{"type": "auth", "token": "%s"}' % TOKEN,
    '{"type": "frame", "seq": "x"}',
    '{"type": "frame", "seq": -1}',
    '{"type": "frame", "seq": true}',
    '{"type": "frame"}',
    '[1, 2]',
])
def test_bad_text_messages(make_client, message):
    with make_client() as client, authed(client) as ws:
        ws.send_text(message)
        assert ws.receive_json() == BAD_MESSAGE
        assert send_frame(ws, 9)["seq"] == 9


def test_binary_without_frame_is_bad_message(make_client):
    with make_client() as client, authed(client) as ws:
        ws.send_bytes(JPEG)
        assert ws.receive_json() == BAD_MESSAGE


def test_bad_message_discards_pending_frame(make_client):
    with make_client() as client, authed(client) as ws:
        ws.send_json({"type": "frame", "seq": 5})
        ws.send_text("hello")
        assert ws.receive_json() == BAD_MESSAGE
        ws.send_bytes(JPEG)  # 5번 frame은 버려졌다
        assert ws.receive_json() == BAD_MESSAGE


def test_consecutive_frames_use_latest_seq(make_client):
    with make_client() as client, authed(client) as ws:
        ws.send_json({"type": "frame", "seq": 1})
        ws.send_json({"type": "frame", "seq": 2})
        ws.send_bytes(JPEG)
        assert ws.receive_json()["seq"] == 2


def test_model_calls_never_overlap_during_replacement(make_client):
    lock = threading.Lock()
    calls = {"now": 0, "max": 0}

    def slow(img):
        with lock:
            calls["now"] += 1
            calls["max"] = max(calls["max"], calls["now"])
        time.sleep(0.3)
        with lock:
            calls["now"] -= 1
        return fake_predictor(img)

    with make_client(predictor=slow) as client, authed(client) as a:
        a.send_json({"type": "frame", "seq": 1})
        a.send_bytes(JPEG)
        time.sleep(0.1)  # a의 추론이 스레드에서 도는 중
        with authed(client) as b:
            with pytest.raises(WebSocketDisconnect) as exc:
                a.receive_json()
            assert exc.value.code == 4010
            assert send_frame(b, 2)["seq"] == 2
    assert calls["max"] == 1


def test_predictor_exception_is_server_error_and_keeps_connection(make_client):
    calls = {"n": 0}

    def flaky(img):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("CUDA out of memory")
        return fake_predictor(img)

    with make_client(predictor=flaky) as client, authed(client) as ws:
        assert send_frame(ws, 1) == {"type": "error", "code": "server_error", "seq": 1}
        assert send_frame(ws, 2)["type"] == "result"


def test_health_and_no_docs(make_client):
    with make_client() as client:
        assert client.get("/health").json() == {"ok": True}
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
