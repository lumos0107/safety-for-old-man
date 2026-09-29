from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings

TOKEN = "t" * 43
ORIGIN = "https://lumos0107.github.io"
READY = {"type": "ready", "model": "yolo11n-pose"}


def fake_predictor(img):
    return {"img_w": int(img.shape[1]), "img_h": int(img.shape[0]), "infer_ms": 1.0, "people": []}


@pytest.fixture
def make_client():
    def _make(predictor=fake_predictor, **overrides):
        settings = Settings(token=TOKEN, allowed_origins=frozenset({ORIGIN}), **overrides)
        return TestClient(create_app(settings, predictor))
    return _make


@contextmanager
def authed(client, **kw):
    with client.websocket_connect("/ws", **kw) as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        assert ws.receive_json() == READY
        yield ws
