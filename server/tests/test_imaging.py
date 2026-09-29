import io
import time

import pytest
from PIL import Image

from server.imaging import BadImage, decode_jpeg
from server.tests.helpers import jpeg_bytes, jpeg_with_fake_size


def test_decodes_to_bgr_array():
    img = decode_jpeg(jpeg_bytes(64, 48, color=(255, 0, 0)))
    assert img.shape == (48, 64, 3)
    assert img.dtype.name == "uint8"
    b, g, r = img[24, 32]
    assert r > 200 and b < 60  # RGB 빨강 → BGR 마지막 채널


def test_exact_max_side_allowed():
    assert decode_jpeg(jpeg_bytes(2000, 8)).shape == (8, 2000, 3)


@pytest.mark.parametrize("data", [
    b"not a jpeg",
    b"",
    jpeg_bytes(2001, 8),               # 실제로 긴 변 초과
    jpeg_bytes(64, 48)[:200],          # 잘린 파일
])
def test_rejects_bad_input(data):
    with pytest.raises(BadImage):
        decode_jpeg(data)


def test_rejects_png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    with pytest.raises(BadImage):
        decode_jpeg(buf.getvalue())


@pytest.mark.parametrize("w,h", [(30000, 30000), (2001, 16), (16, 60000)])
def test_rejects_fake_header_size_without_decoding(w, h):
    start = time.perf_counter()
    with pytest.raises(BadImage):
        decode_jpeg(jpeg_with_fake_size(w, h))
    assert time.perf_counter() - start < 0.5  # 메모리를 잡아먹는 디코딩을 하지 않았다
