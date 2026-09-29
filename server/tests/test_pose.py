import cv2
import numpy as np
import pytest
from ultralytics.utils import ASSETS

from server.pose import PoseModel


@pytest.fixture(scope="module")
def model():
    return PoseModel()


def test_bus_people_and_keypoints(model):
    img = cv2.imread(str(ASSETS / "bus.jpg"))
    r = model.predict(img)
    assert (r["img_w"], r["img_h"]) == (810, 1080)
    assert r["infer_ms"] > 0
    assert len(r["people"]) >= 3
    for p in r["people"]:
        assert 0.5 <= p["score"] <= 1
        assert len(p["box"]) == 4 and all(0 <= v <= 1 for v in p["box"])
        assert len(p["kpts"]) == 17
        for x, y, c in p["kpts"]:
            assert 0 <= x <= 1 and 0 <= y <= 1 and 0 <= c <= 1


def test_empty_scene_returns_no_people(model):
    r = model.predict(np.zeros((480, 640, 3), np.uint8))
    assert r["people"] == [] and (r["img_w"], r["img_h"]) == (640, 480)
