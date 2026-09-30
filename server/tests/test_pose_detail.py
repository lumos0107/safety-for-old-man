"""관절 26점 전처리·후처리·묶음·대비 동작. 설계 6.1·6.5·6.7"""
import hashlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from server import pose_detail as pd

REF = Path(__file__).parent / "data" / "rtmpose_ref.npz"
ROOT = Path(__file__).resolve().parents[2]
HAVE_MODEL = pd.MODEL_PATH.exists()


# ---------- 전처리 ----------

def test_center_scale_pads_and_fixes_aspect():
    c, s = pd.box_center_scale([10, 20, 110, 220])  # 100 x 200
    assert np.allclose(c, [60, 120])
    assert np.allclose(s, [187.5, 250])  # 1.25배 → 125 x 250, 192:256에 맞춰 가로를 늘림
    c, s = pd.box_center_scale([0, 0, 400, 100])  # 가로로 긴 상자(누운 사람)
    assert np.allclose(s, [500, 500 / 0.75])


def test_warp_round_trip():
    c, s = pd.box_center_scale([100, 50, 300, 450])
    fwd, inv = pd.warp_matrix(c, s), pd.warp_matrix(c, s, inverse=True)
    pts = np.array([[150.0, 60.0], [290.0, 440.0], [200.0, 250.0]])
    there = pts @ fwd[:, :2].T + fwd[:, 2]
    back = there @ inv[:, :2].T + inv[:, 2]
    assert np.abs(back - pts).max() < 1e-6
    assert np.allclose(pts[2] @ fwd[:, :2].T + fwd[:, 2], [96, 128])  # 상자 중심 → 입력 가운데


def test_crop_shape_and_normalization():
    img = np.full((100, 80, 3), 128, np.uint8)
    x, c, s = pd.crop(img, [0, 0, 40, 40])  # 넓힌 영역의 왼쪽 위가 이미지 밖으로 나간다
    assert x.shape == (3, 256, 192) and x.dtype == np.float32
    mid = x[:, 128, 96]
    assert np.allclose(mid, (128 - np.array(pd.MEAN)) / np.array(pd.STD), atol=1e-5)
    corner = x[:, 0, 0]  # 이미지 밖은 0(검정)으로 채운 뒤 정규화
    assert np.allclose(corner, (0 - np.array(pd.MEAN)) / np.array(pd.STD), atol=1e-5)


# ---------- 복원 ----------

def simcc(peaks_x, peaks_y, val=0.8, nx=384, ny=512):
    sx = np.zeros((1, len(peaks_x), nx), np.float32)
    sy = np.zeros((1, len(peaks_y), ny), np.float32)
    for k, (px, py) in enumerate(zip(peaks_x, peaks_y)):
        sx[0, k, px] = val
        sy[0, k, py] = val + 0.2
    return sx, sy


def test_restore_coordinates_and_scores():
    c, s = np.array([100.0, 200.0]), np.array([192.0, 256.0])  # 맞춘 크기 = 입력 크기 → 1칸 = 0.5px
    sx, sy = simcc([192, 0], [256, 511])
    kpts, scores, locs = pd.restore(sx, sy, c[None], s[None])
    assert np.allclose(kpts[0, 0], [100, 200])  # 가운데 칸 → 상자 중심
    assert np.allclose(kpts[0, 1], [100 - 96, 200 - 128 + 511 / 2])
    assert np.allclose(scores[0], [0.9, 0.9])  # x·y 최댓값의 평균
    assert locs[0, 1].tolist() == [0, 511]


def test_restore_nonpositive_score_marks_invalid():
    sx, sy = simcc([100], [100], val=-0.5)
    kpts, scores, locs = pd.restore(sx, sy, np.array([[50.0, 50.0]]), np.array([[192.0, 256.0]]))
    assert scores[0, 0] <= 0 and locs[0, 0].tolist() == [-1, -1]


# ---------- 정리 ----------

def clean_one(x, y, score, loc=(100, 100), w=200, h=100):
    return pd.clean(np.array([[[x, y]]], float), np.array([[score]], float), np.array([[loc]]), w, h)[0][0]


@pytest.mark.parametrize("loc", [(0, 100), (1, 100), (382, 100), (383, 100), (100, 0), (100, 1), (100, 510), (100, 511)])
def test_edge_bins_get_zero_score(loc):
    assert clean_one(50, 50, 0.9, loc)[2] == 0


@pytest.mark.parametrize("loc", [(2, 100), (381, 100), (100, 2), (100, 509)])
def test_bins_next_to_edge_keep_score(loc):
    assert clean_one(50, 50, 0.9, loc)[2] == 0.9


def test_outside_image_gets_zero_and_is_clamped():
    assert clean_one(200 * 1.006, 40, 0.62) == [1.0, 0.4, 0]  # 4장 사례: 0.6% 밖
    assert clean_one(50, 100 * 1.013, 0.70) == [0.25, 1.0, 0]  # 1.3% 밖
    assert clean_one(-0.5, 40, 0.9)[2] == 0
    assert clean_one(200 * 0.99, 100 * 0.99, 0.8) == [0.99, 0.99, 0.8]


def test_score_clipped_to_one():
    assert clean_one(100, 50, 1.11)[2] == 1.0


# ---------- 묶음 ----------

class FakeSession:
    def __init__(self, provider="CUDAExecutionProvider", fail=False):
        self.provider, self.fail, self.batches = provider, fail, []

    def get_providers(self):
        return [self.provider, "CPUExecutionProvider"]

    def get_inputs(self):
        return [type("I", (), {"name": "input"})()]

    def run(self, _names, feed):
        if self.fail:
            raise RuntimeError("boom")
        n = feed["input"].shape[0]
        self.batches.append(n)
        sx = np.zeros((n, 26, 384), np.float32)
        sy = np.zeros((n, 26, 512), np.float32)
        sx[:, :, 192] = 0.9
        sy[:, :, 256] = 0.9
        return [sx, sy]


IMG = np.zeros((480, 640, 3), np.uint8)
BOX = [100.0, 100.0, 200.0, 300.0]


def test_no_people_skips_model():
    s = FakeSession()
    assert pd.DetailModel(s)(IMG, []) == []
    assert s.batches == []


@pytest.mark.parametrize("n,gpu_batches,cpu_batches", [(1, [4], [1]), (3, [4], [3]), (5, [4, 4], [4, 1])])
def test_gpu_pads_to_four_cpu_does_not(n, gpu_batches, cpu_batches):
    for provider, expected in [("CUDAExecutionProvider", gpu_batches), ("CPUExecutionProvider", cpu_batches)]:
        s = FakeSession(provider)
        out = pd.DetailModel(s)(IMG, [BOX] * n)
        assert s.batches == expected
        assert len(out) == n and all(len(k) == 26 for k in out)
        assert out[0][0][:2] == [round(150 / 640, 4), round(200 / 480, 4)]  # 가운데 칸 → 상자 중심


def test_warmup_uses_real_batch_size():
    for provider, size in [("CUDAExecutionProvider", 4), ("CPUExecutionProvider", 1)]:
        s = FakeSession(provider)
        pd.DetailModel(s).warmup()
        assert s.batches == [size]


# ---------- 불러오기와 대비 동작 ----------

def test_load_missing_file(tmp_path):
    model, status = pd.load(tmp_path / "none.onnx", "0" * 64)
    assert model is None and "파일이 없" in status and "fetch_models" in status


def test_load_hash_mismatch(tmp_path):
    f = tmp_path / "m.onnx"
    f.write_bytes(b"x")
    model, status = pd.load(f, "0" * 64)
    assert model is None and "해시" in status


def _good_file(tmp_path):
    f = tmp_path / "m.onnx"
    f.write_bytes(b"x")
    return f, hashlib.sha256(b"x").hexdigest()


def test_load_session_creation_failure(tmp_path):
    f, sha = _good_file(tmp_path)

    def boom(_path):
        raise RuntimeError("bad model")
    model, status = pd.load(f, sha, make_session=boom)
    assert model is None and "세션" in status and "RuntimeError" in status


def test_load_warmup_failure(tmp_path):
    f, sha = _good_file(tmp_path)
    model, status = pd.load(f, sha, make_session=lambda _p: FakeSession(fail=True))
    assert model is None and "예열" in status


def test_load_ok_reports_device(tmp_path):
    f, sha = _good_file(tmp_path)
    model, status = pd.load(f, sha, make_session=lambda _p: FakeSession())
    assert model is not None and model.gpu and status == "관절 26점 (RTMPose-m, GPU)"
    model, status = pd.load(f, sha, make_session=lambda _p: FakeSession("CPUExecutionProvider"))
    assert model is not None and not model.gpu
    assert status.startswith("관절 26점 (RTMPose-m, CPU)") and "30ms" in status


def test_server_code_does_not_import_rtmlib():
    code = "import sys, server.pose_detail, server.pose, server.app; print('rtmlib' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr


# ---------- 원본 대조 (rtmlib 기준값, 설계 6.7) ----------

needs_ref = pytest.mark.skipif(not REF.exists(), reason="기준값 없음 (design/eval/body_detail/make_reference.py)")


def _rgb(name):
    from ultralytics.utils import ASSETS

    from server.imaging import decode_jpeg
    return np.ascontiguousarray(decode_jpeg((ASSETS / f"{name}.jpg").read_bytes())[:, :, ::-1])


@needs_ref
def test_reference_preprocess():
    ref = np.load(REF)
    x, c, s = pd.crop(_rgb("bus"), ref["pre_box"].tolist())
    hwc = x.transpose(1, 2, 0)
    assert np.allclose(c, ref["pre_center"]) and np.allclose(s, ref["pre_scale"])
    assert np.abs(hwc.mean((0, 1)) - ref["pre_mean"]).max() < 1e-3
    assert np.abs(hwc.std((0, 1)) - ref["pre_std"]).max() < 1e-3
    i = ref["pre_idx"]
    assert np.abs(hwc[i[:, 0], i[:, 1], i[:, 2]] - ref["pre_vals"]).max() < 1e-3


@needs_ref
def test_reference_restore():
    ref = np.load(REF)
    kpts, scores, _ = pd.restore(ref["post_simcc_x"], ref["post_simcc_y"], ref["post_center"][None], ref["post_scale"][None])
    assert np.abs(kpts - ref["post_kpts"]).max() < 1e-4
    assert np.abs(scores - ref["post_scores"]).max() < 1e-6


@needs_ref
@pytest.mark.skipif(not HAVE_MODEL, reason="모델 파일 없음 (tools/fetch_models.py)")
@pytest.mark.parametrize("name", ["bus", "zidane"])
def test_reference_end_to_end_gpu(name):
    ref = np.load(REF)
    model, status = pd.load()
    if model is None or not model.gpu:
        pytest.skip(f"GPU 세션이 아님: {status}")
    boxes = ref[f"e2e_{name}_boxes"]
    kpts, scores, _ = model.raw(_rgb(name), boxes.tolist())  # 복원 단계 출력 (정리 전)
    rk, rs = ref[f"e2e_{name}_kpts"], ref[f"e2e_{name}_scores"]
    for i, box in enumerate(boxes):
        _, s = pd.box_center_scale(box)
        tol = s[0] / 192 * 2  # SimCC 2칸
        sel = rs[i] >= 0.4
        assert np.abs(kpts[i][sel] - rk[i][sel]).max() <= tol, (name, i)
        assert np.abs(scores[i][sel] - rs[i][sel]).max() <= 0.01, (name, i)


# ---------- 실제 모델 ----------

@pytest.mark.skipif(not HAVE_MODEL, reason="모델 파일 없음 (tools/fetch_models.py)")
def test_real_model_bus_anatomy():
    from ultralytics.utils import ASSETS

    from server.pose import PoseModel
    import cv2
    model, status = pd.load()
    assert model is not None, status
    r = PoseModel(detail=model).predict(cv2.imread(str(ASSETS / "bus.jpg")))
    assert r["layout"] == "halpe26" and len(r["people"]) >= 3
    for p in r["people"]:
        k = p["kpts"]
        assert len(k) == 26
        if min(k[i][2] for i in (0, 5, 6, 15, 16, 17, 18, 20, 21, 24, 25)) < 0.4:
            continue  # 가려진 사람(버스 옆 반쯤 잘린 사람)은 건너뛴다
        assert k[17][1] < k[0][1]  # 머리 꼭대기가 코보다 위
        if abs(k[5][0] - k[6][0]) > 0.05:  # 옆모습(두 어깨가 겹침)은 빼고
            assert min(k[5][0], k[6][0]) <= k[18][0] <= max(k[5][0], k[6][0])  # 목이 두 어깨 사이
        # 발 점은 같은 쪽 발목 가까이 (걸을 때는 발끝이 발목보다 올라갈 수 있어 위아래는 보지 않는다)
        near = lambda a, b: np.hypot((a[0] - b[0]) * 810, (a[1] - b[1]) * 1080) < 108  # 이미지 높이의 10%
        assert all(near(k[i], k[15]) for i in (20, 22, 24)) and all(near(k[i], k[16]) for i in (21, 23, 25))
        assert not all(near(k[i], k[16]) for i in (20, 22, 24)) or abs(k[15][0] - k[16][0]) < 0.05  # 좌우가 뒤바뀌지 않음


# ---------- 성능 (설계 6.6) ----------

@pytest.mark.skipif(not HAVE_MODEL, reason="모델 파일 없음 (tools/fetch_models.py)")
def test_people_count_changes_stay_within_budget():
    import cv2
    from ultralytics.utils import ASSETS

    from server.pose import PoseModel
    model, status = pd.load()
    if model is None or not model.gpu:
        pytest.skip(f"GPU 세션이 아님: {status}")
    pm = PoseModel(detail=model)
    bus = cv2.imread(str(ASSETS / "bus.jpg"))
    two = cv2.imread(str(ASSETS / "zidane.jpg"))
    one = np.ascontiguousarray(bus[399:905, 47:243])  # 왼쪽 사람 한 명 (YOLO 상자 영역)
    scenes = {1: one, 2: two, 3: bus}
    counts = {k: len(pm.predict(v)["people"]) for k, v in scenes.items()}
    assert counts[1] == 1 and counts[2] == 2 and counts[3] >= 3, counts
    budget = {1: 20, 2: 30, 3: 30}
    times = []
    for n in [1, 2, 1, 3, 2, 1, 3, 3, 1, 2] * 2:
        r = pm.predict(scenes[n])
        times.append((n, r["infer_ms"]))
        assert r["layout"] == "halpe26"
    # 인원별 중앙값은 목표 안, 한 번 한 번도 목표+10ms 안 — 묶음 크기 전환 멈춤(60ms 넘음)은 잡고,
    # 운영체제·GPU 때문에 가끔 1~3ms 넘는 흔들림으로는 실패하지 않게 한다 (2026-09-30 실측 최대 22.5ms)
    for n in budget:
        mine = sorted(t for k, t in times if k == n)
        assert mine[len(mine) // 2] <= budget[n], (n, times)
        assert mine[-1] <= budget[n] + 10, (n, times)


def test_build_app_falls_back_to_17_when_detail_unavailable(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    import server.app as app_module
    monkeypatch.setenv("TOKEN", "b" * 43)
    monkeypatch.delenv("DETAIL", raising=False)
    monkeypatch.setattr(pd, "load", lambda: (None, "모델 파일이 없습니다 (x.onnx). 해결: fetch_models"))
    caplog.set_level("WARNING", logger="pose")
    app = app_module.build_app()
    with TestClient(app):
        pass
    assert "17점으로 동작합니다" in caplog.text and "fetch_models" in caplog.text
