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


# ---------- 관절 26점 연결 (설계 6.2·6.5) ----------

BUS = None


def bus():
    global BUS
    if BUS is None:
        BUS = cv2.imread(str(ASSETS / "bus.jpg"))
    return BUS


class FakeDetail:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def __call__(self, img, boxes):
        self.calls.append([list(b) for b in boxes])
        if self.fail:
            raise RuntimeError("cuda oom")
        return [[[0.5, 0.5, 0.9]] * 26 for _ in boxes]


@pytest.fixture
def fresh(model):
    model.detail, model.detail_failures = None, 0
    yield model
    model.detail, model.detail_failures = None, 0


def test_without_detail_layout_is_coco17(fresh):
    r = fresh.predict(bus())
    assert r["layout"] == "coco17" and all(len(p["kpts"]) == 17 for p in r["people"])


def test_detail_gets_pixel_boxes_and_sets_layout(fresh):
    fresh.detail = FakeDetail()
    r = fresh.predict(bus())
    assert r["layout"] == "halpe26" and all(len(p["kpts"]) == 26 for p in r["people"])
    (boxes,) = fresh.detail.calls
    assert len(boxes) == len(r["people"])
    for b, p in zip(boxes, r["people"]):  # 비율 상자와 같은 사람의 픽셀 상자
        assert abs(b[0] / 810 - p["box"][0]) < 1e-3 and abs(b[3] / 1080 - p["box"][3]) < 1e-3


def test_no_people_skips_detail_but_keeps_layout(fresh):
    fresh.detail = FakeDetail()
    r = fresh.predict(np.zeros((480, 640, 3), np.uint8))
    assert r["people"] == [] and r["layout"] == "halpe26" and fresh.detail.calls == []


def test_detail_failure_falls_back_per_frame_then_turns_off(fresh, caplog):
    caplog.set_level("WARNING", logger="pose")
    bad = FakeDetail(fail=True)
    fresh.detail = bad
    for i in range(4):
        r = fresh.predict(bus())
        assert r["layout"] == "coco17" and all(len(p["kpts"]) == 17 for p in r["people"])
        assert fresh.detail is bad
    fresh.detail = FakeDetail()  # 성공하면 연속 실패 수가 초기화된다
    assert fresh.predict(bus())["layout"] == "halpe26"
    fresh.detail = bad
    for _ in range(5):
        r = fresh.predict(bus())
    assert r["layout"] == "coco17" and fresh.detail is None  # 연속 5회 → 26점 끔
    assert "RuntimeError" in caplog.text and "17점으로 계속" in caplog.text
    assert fresh.predict(bus())["layout"] == "coco17"


def test_infer_ms_is_wall_time_including_detail(fresh):
    import time

    class Slow(FakeDetail):
        def __call__(self, img, boxes):
            time.sleep(0.05)
            return super().__call__(img, boxes)
    fresh.detail = Slow()
    assert fresh.predict(bus())["infer_ms"] >= 50
