"""테스트 전용 도우미. 이미지는 메모리에서만 만든다 (저장소에 사진을 넣지 않는다)."""
import io

from PIL import Image


def jpeg_bytes(w: int = 64, h: int = 48, color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def jpeg_with_fake_size(w: int, h: int) -> bytes:
    """실제로는 16x16인 JPEG의 SOF0 헤더에 가짜 해상도를 적는다 (압축 폭탄 흉내)."""
    data = bytearray(jpeg_bytes(16, 16))
    i = data.index(b"\xff\xc0")
    data[i + 5:i + 7] = h.to_bytes(2, "big")
    data[i + 7:i + 9] = w.to_bytes(2, "big")
    return bytes(data)
