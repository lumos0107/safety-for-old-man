"""받은 JPEG 바이트를 모델 입력(BGR 배열)으로 바꾼다. 디스크에 쓰지 않는다."""
import io

import numpy as np
from PIL import Image


class BadImage(ValueError):
    """디코딩할 수 없거나 허용 범위를 벗어난 이미지."""


def decode_jpeg(data: bytes, max_side: int = 2000) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(data))  # 여기서는 헤더만 읽는다
        if img.format != "JPEG":
            raise BadImage("JPEG가 아님")
        w, h = img.size
        if min(w, h) < 1 or max(w, h) > max_side:
            raise BadImage("해상도 범위 밖")
        rgb = img.convert("RGB")  # 실제 디코딩
    except BadImage:
        raise
    except Exception as exc:  # 깨진 파일, 잘린 파일, PIL 압축 폭탄 오류
        raise BadImage(str(exc)) from exc
    return np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
