import hashlib
import zipfile

import pytest

from tools.fetch_models import FetchError, extract_model

ONNX = b"fake onnx bytes" * 100


def make_zip(tmp_path, members):
    path = tmp_path / "m.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_extracts_only_end2end_onnx_under_fixed_name(tmp_path):
    zp = make_zip(tmp_path, {"rtmpose-m/end2end.onnx": ONNX, "rtmpose-m/pipeline.json": b"{}",
                             "rtmpose-m/../../evil.txt": b"x"})
    dest = tmp_path / "models" / "rtmpose-m_halpe26.onnx"
    extract_model(zp, dest, sha(ONNX))
    assert dest.read_bytes() == ONNX
    assert sorted(p.name for p in dest.parent.iterdir()) == ["rtmpose-m_halpe26.onnx"]  # 다른 파일은 풀지 않는다
    assert not (tmp_path / "evil.txt").exists()


def test_wrong_hash_leaves_no_file(tmp_path):
    zp = make_zip(tmp_path, {"a/end2end.onnx": ONNX})
    dest = tmp_path / "models" / "rtmpose-m_halpe26.onnx"
    with pytest.raises(FetchError, match="해시"):
        extract_model(zp, dest, "0" * 64)
    assert not dest.exists()
    assert not any(dest.parent.iterdir())  # 임시 파일도 남지 않는다


def test_wrong_hash_keeps_existing_good_file(tmp_path):
    dest = tmp_path / "rtmpose-m_halpe26.onnx"
    dest.write_bytes(b"old good")
    zp = make_zip(tmp_path, {"a/end2end.onnx": ONNX})
    with pytest.raises(FetchError):
        extract_model(zp, dest, "0" * 64)
    assert dest.read_bytes() == b"old good"


@pytest.mark.parametrize("members", [{"a/model.onnx": ONNX}, {"a/end2end.onnx": ONNX, "b/end2end.onnx": ONNX}])
def test_zero_or_many_end2end_is_error(tmp_path, members):
    with pytest.raises(FetchError, match="end2end.onnx"):
        extract_model(make_zip(tmp_path, members), tmp_path / "out.onnx", sha(ONNX))


def test_not_a_zip_is_error(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"<html>not found</html>")
    with pytest.raises(FetchError, match="zip"):
        extract_model(bad, tmp_path / "out.onnx", sha(ONNX))


def test_constants_are_shared_with_server():
    from server import pose_detail
    from tools import fetch_models
    assert fetch_models.MODEL_PATH == pose_detail.MODEL_PATH
    assert pose_detail.MODEL_PATH.name == "rtmpose-m_halpe26.onnx"
    assert pose_detail.MODEL_SHA256 == "26f3a19e61304a600dfb82d1001d41d24343b89fc70a33ffc84657e0b0bf2ecf"
    assert pose_detail.MODEL_URL.startswith("https://download.openmmlab.com/")
