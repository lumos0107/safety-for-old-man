# 웹 카메라 자세 인식 시제품 구현 계획

> **구현 당시(2026-09-29) 기록이다.** 지금의 명령·포트는 README가 기준이다 — 서버 포트는 18080(당시 8000), 로컬 화면은 `tools/serve_page.py`로 띄운다. 이 문서의 `python -m http.server`는 **쓰지 않는다** (저장소 전체를 내줘 `server/.env`의 토큰이 열림).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 폰·노트북 브라우저 카메라 영상을 이 PC의 GPU(YOLO11n-pose)로 보내 관절 17개를 인식하고, 브라우저 화면에 뼈대를 겹쳐 그린다.

**Architecture:** 프런트는 빌드 없는 HTML·JS로 GitHub Pages(저장소 루트)에서 제공한다. 백엔드는 FastAPI WebSocket 서버로 `127.0.0.1:8000`에서만 열고, Tailscale Funnel이 바깥 HTTPS 접속을 이 포트로 넘긴다. 프레임은 한 장 보내고 결과를 받은 뒤 다음 장을 보내며(stop-and-wait), 이미지는 메모리에서만 처리한다.

**Tech Stack:** Python 3.12 (`.venv`, uv), PyTorch cu128, ultralytics 8.4, FastAPI, uvicorn[standard], Pillow, python-dotenv, pytest, Playwright(검증용, 설치된 Chrome 사용), 바닐라 JS(ES 모듈), Node 24 내장 테스트 러너

**Spec:** `design/2026-09-29-web-pose-design.md` (검토: `design/2026-09-29-web-pose-design-review.md`)

**설계 7장 파일 구조에 더하는 파일** (책임 분리와 테스트를 위해)
- `server/config.py`, `server/imaging.py`, `server/pose.py`, `server/sessions.py` — `app.py`에서 설정·이미지 디코딩·모델·연결 관리를 떼어 각각 따로 테스트한다.
- `lib.js`, `web_tests/lib.test.mjs`, `package.json` — 프런트의 순수 함수(종료 코드 판단, 주소 정규화, 좌표 변환)를 Node로 테스트한다.
- `tools/e2e_browser.py` — 가짜 카메라(예시 사진)로 브라우저 전체 경로를 자동 검증한다.
- `pytest.ini`, `.nojekyll`, `README.md` 사용법

## Global Constraints

- 모든 명령은 저장소 루트 `capston_iecc/vision_proto`에서 실행한다. 파이썬은 `.venv\Scripts\python` (Python 3.12, torch `2.11.0+cu128` 설치됨).
- 백엔드는 `--host 127.0.0.1 --port 8000 --ws-max-size 2097152 --log-level warning`으로만 띄운다.
- 종료 코드: `4001` 토큰 오류, `4003` 허용되지 않은 출처, `4008` 인증 시간 초과, `4009` 인증 대기 초과로 밀려남, `4010` 다른 연결이 대체.
- 수치: 인증 대기 3초, 인증 대기 연결 최대 4개, 앱 크기 한도 1,048,576바이트(초과 시 `too_large`), 긴 변 2000px 초과 `bad_image`, 프런트 응답 대기 5초, 재연결 3초부터 최대 10초, 전송 JPEG 긴 변 640px·품질 0.7, 키포인트 표시 신뢰도 0.5, 모델 `yolo11n-pose` `imgsz=640`, 사람 검출 신뢰도 0.5.
- 이미지를 디스크·로그에 남기지 않는다. ultralytics 호출은 항상 `verbose=False, save=False`.
- 토큰은 `secrets.token_urlsafe(32)`(43자) 이상, 32자 미만이면 서버가 시작을 거부한다. 토큰·서버 주소는 저장소에 넣지 않는다. 프런트는 `localStorage`에만 저장한다.
- `ALLOWED_ORIGINS` 기본값은 `https://lumos0107.github.io` 하나. `http://localhost:5500`은 `.env.example`과 검증 도구에만 쓴다.
- FastAPI 문서 경로는 끈다 (`docs_url=None, redoc_url=None, openapi_url=None`). `/health`는 `{"ok": true}`만.
- 프런트 `localStorage` 키는 `pose.serverUrl`, `pose.token`. 서버 주소는 `https`/`wss`만, `http`/`ws`는 `localhost`·`127.0.0.1`·`[::1]`만 허용.
- 화면 상태 문구는 정확히: `설정 필요` `연결 중` `연결됨` `토큰 확인` `허용되지 않은 주소` `다른 기기에서 사용 중` `서버 꺼짐` (그 밖에 `대기`, `정지`).
- 프런트는 프레임워크·빌드 없이 `index.html`·`style.css`·`app.js`·`lib.js`.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. `git push`는 Task 11에서만 한다.

## Review Focus

1. **서버 재시작·네트워크 전환 중 사용**: 서버가 죽었다 살아나도 페이지를 새로고침하지 않고 `서버 꺼짐` → `연결됨`으로 돌아와야 한다. → Task 10 e2e에서 서버를 죽였다 다시 띄운다.
2. **토큰을 복사해 붙일 때 앞뒤 공백·줄바꿈**: 그래도 인증돼야 한다. → Task 1(`TOKEN` 환경변수 앞뒤 공백 제거), Task 8(`cleanToken`), Task 10(공백 붙은 토큰 입력).
3. **서버 주소를 여러 형태로 입력** (호스트만, 끝의 `/`, `/ws` 포함, 로컬 `http://`): 모두 올바른 `wss://…/ws`·`ws://…/ws`가 돼야 한다. → Task 8 `normalizeServerUrl` 테스트.
4. **폰을 세운 세로 영상과 레터박스**: 뼈대가 사람 위에 정확히 겹쳐야 한다. → Task 8 `fitContain` 세로·가로 테스트, Task 10 세로(480×640) 가짜 카메라 + 스크린샷 확인.
5. **대체된 연결의 정리 코드가 새 연결의 기록을 지우지 않음**: 세 번째 기기가 들어오면 두 번째가 `4010`으로 밀려나야 한다. 두 탭이 서로 뺏는 반복도 없어야 한다. → Task 4 `discard` 테스트, Task 5 세 연결 테스트, Task 10 두 페이지 테스트.

---

### Task 1: 기반 — 의존성, 설정 로딩, 토큰 생성

**Files:**
- Create: `server/__init__.py`, `server/tests/__init__.py`, `server/config.py`, `server/requirements.txt`, `server/.env.example`, `tools/gen_token.py`, `pytest.ini`
- Move: `webcam_pose.py` → `tools/webcam_pose.py`
- Test: `server/tests/test_config.py`

**Interfaces:**
- Produces: `server.config.Settings` (frozen dataclass: `token: str`, `allowed_origins: frozenset[str]`, `auth_timeout: float = 3.0`, `max_pending: int = 4`, `max_bytes: int = 1_048_576`, `max_side: int = 2000`, `model: str = "yolo11n-pose.pt"`, property `model_name -> str` = 파일 이름에서 확장자 뺀 것), `server.config.load_settings(env_file: Path | None = DEFAULT_ENV) -> Settings` (환경변수가 `.env`보다 우선, 전역 환경 변경 없음), `server.config.DEFAULT_ENV = server/.env`
- `tools/gen_token.py [--env PATH] [--force]` — `.env` 작성, 토큰 출력

- [ ] **Step 1: 의존성 설치**

`server/requirements.txt`:
```
# PyTorch(CUDA)는 먼저 따로 설치한다:
#   uv venv --python 3.12 .venv
#   uv pip install --python .venv torch torchvision --index-url https://download.pytorch.org/whl/cu128
#   uv pip install --python .venv -r server/requirements.txt
# 정확한 버전은 server/requirements.lock.txt (uv pip freeze 결과)
ultralytics~=8.4.0
opencv-python
pillow
numpy
fastapi
uvicorn[standard]
python-dotenv
websockets>=13
# 테스트·검증
pytest
httpx
playwright
```

Run: `uv pip install --python .venv -r server/requirements.txt`
Expected: 설치 완료. 이어서 `.venv\Scripts\python -c "import fastapi, uvicorn, dotenv, websockets, playwright; print(websockets.__version__)"` 가 13 이상 버전을 출력.

Run: `uv pip freeze --python .venv > server/requirements.lock.txt`
Expected: `torch==2.11.0+cu128`, `ultralytics==8.4.x` 등이 담긴 잠금 파일 (팀원이 같은 버전을 재현할 때 쓴다).

- [ ] **Step 2: 패키지 뼈대와 pytest 설정**

`server/__init__.py`, `server/tests/__init__.py`: 빈 파일.

`pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = server/tests
```

`git mv webcam_pose.py tools/webcam_pose.py`는 파일이 아직 추적되지 않으므로 대신:
Run: `mkdir -p tools && mv webcam_pose.py tools/webcam_pose.py`

- [ ] **Step 3: 실패하는 테스트 작성**

`server/tests/test_config.py`:
```python
import subprocess
import sys
from pathlib import Path

import pytest

from server.config import Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]
GOOD = "a" * 43


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("TOKEN", "ALLOWED_ORIGINS", "AUTH_TIMEOUT", "MODEL"):
        monkeypatch.delenv(key, raising=False)


def test_env_values(monkeypatch):
    monkeypatch.setenv("TOKEN", GOOD)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.io/, http://localhost:5500")
    monkeypatch.setenv("AUTH_TIMEOUT", "0.5")
    s = load_settings(env_file=None)
    assert s.token == GOOD
    assert s.allowed_origins == frozenset({"https://a.io", "http://localhost:5500"})
    assert s.auth_timeout == 0.5
    assert s.model_name == "yolo11n-pose"


def test_default_origin_is_pages_only(monkeypatch):
    monkeypatch.setenv("TOKEN", GOOD)
    assert load_settings(env_file=None).allowed_origins == frozenset({"https://lumos0107.github.io"})


def test_token_whitespace_is_stripped(monkeypatch):
    monkeypatch.setenv("TOKEN", f"  {GOOD}\n")
    assert load_settings(env_file=None).token == GOOD


@pytest.mark.parametrize("token", [None, "", "short-token"])
def test_missing_or_short_token_refused(monkeypatch, token):
    if token is not None:
        monkeypatch.setenv("TOKEN", token)
    with pytest.raises(RuntimeError, match="TOKEN"):
        load_settings(env_file=None)


def test_env_file_used_and_env_var_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"TOKEN={GOOD}\nALLOWED_ORIGINS=https://file.io\n", encoding="utf-8")
    assert load_settings(env_file=env).allowed_origins == frozenset({"https://file.io"})
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://env.io")
    assert load_settings(env_file=env).allowed_origins == frozenset({"https://env.io"})


def test_settings_defaults():
    s = Settings(token=GOOD, allowed_origins=frozenset())
    assert (s.auth_timeout, s.max_pending, s.max_bytes, s.max_side) == (3.0, 4, 1_048_576, 2000)


def run_gen(*args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "gen_token.py"), *args],
                          capture_output=True, text=True, encoding="utf-8")


def test_gen_token_writes_env_and_refuses_overwrite(tmp_path):
    env = tmp_path / ".env"
    first = run_gen("--env", str(env))
    assert first.returncode == 0
    lines = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert len(lines["TOKEN"]) >= 43
    assert lines["ALLOWED_ORIGINS"] == "https://lumos0107.github.io"
    assert lines["TOKEN"] in first.stdout

    again = run_gen("--env", str(env))
    assert again.returncode != 0
    assert run_gen("--env", str(env), "--force").returncode == 0
    new = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert new["TOKEN"] != lines["TOKEN"]  # --force는 토큰을 실제로 교체한다
```

- [ ] **Step 4: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.config'`

- [ ] **Step 5: 구현**

`server/config.py`:
```python
"""서버 설정. 환경변수가 server/.env보다 우선한다."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV = Path(__file__).resolve().parent / ".env"
DEFAULT_ORIGINS = "https://lumos0107.github.io"
MIN_TOKEN_LEN = 32


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_origins: frozenset[str]
    auth_timeout: float = 3.0
    max_pending: int = 4
    max_bytes: int = 1_048_576
    max_side: int = 2000
    model: str = "yolo11n-pose.pt"

    @property
    def model_name(self) -> str:
        return Path(self.model).stem


def load_settings(env_file: Path | None = DEFAULT_ENV) -> Settings:
    values = dict(dotenv_values(env_file)) if env_file and Path(env_file).exists() else {}
    values.update(os.environ)  # os.environ을 바꾸지 않고 합친다

    token = (values.get("TOKEN") or "").strip()
    if len(token) < MIN_TOKEN_LEN:
        raise RuntimeError("TOKEN이 없거나 너무 짧습니다. tools/gen_token.py로 만드세요.")

    raw = values.get("ALLOWED_ORIGINS") or DEFAULT_ORIGINS
    origins = frozenset(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
    return Settings(
        token=token,
        allowed_origins=origins,
        auth_timeout=float(values.get("AUTH_TIMEOUT") or 3.0),
        model=values.get("MODEL") or "yolo11n-pose.pt",
    )
```

`tools/gen_token.py`:
```python
"""접속 토큰을 만들어 server/.env에 쓴다. 출력된 토큰을 브라우저 설정에 넣는다.

사용: .venv\\Scripts\\python tools/gen_token.py [--force] [--env 경로]
"""
import argparse
import secrets
import sys
from pathlib import Path

DEFAULT_ENV = Path(__file__).resolve().parents[1] / "server" / ".env"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", type=Path, default=DEFAULT_ENV)
    ap.add_argument("--force", action="store_true", help="기존 .env를 덮어쓴다 (토큰 교체)")
    args = ap.parse_args()

    if args.env.exists() and not args.force:
        print(f"{args.env}가 이미 있습니다. 토큰을 바꾸려면 --force", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    args.env.write_text(f"TOKEN={token}\nALLOWED_ORIGINS=https://lumos0107.github.io\n", encoding="utf-8")
    print(f"{args.env} 작성 완료. 브라우저 설정에 넣을 토큰:")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`server/.env.example`:
```
# tools/gen_token.py가 이 형식으로 server/.env를 만든다. .env는 저장소에 올리지 않는다.
TOKEN=여기에-43자-이상-토큰
ALLOWED_ORIGINS=https://lumos0107.github.io
# 로컬 개발(python -m http.server 5500) 때만 추가:
# ALLOWED_ORIGINS=https://lumos0107.github.io,http://localhost:5500
```

- [ ] **Step 6: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_config.py -v`
Expected: 9 passed

- [ ] **Step 7: 커밋**

```bash
git add pytest.ini server/__init__.py server/tests/__init__.py server/config.py server/requirements.txt server/requirements.lock.txt server/.env.example server/tests/test_config.py tools/gen_token.py tools/webcam_pose.py
git commit -m "서버 기반: 설정 로딩, 토큰 생성, 의존성

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 이미지 디코딩 (압축 폭탄 방지)

**Files:**
- Create: `server/imaging.py`, `server/tests/helpers.py`
- Test: `server/tests/test_imaging.py`

**Interfaces:**
- Produces: `server.imaging.BadImage(ValueError)`, `server.imaging.decode_jpeg(data: bytes, max_side: int = 2000) -> np.ndarray` (BGR, `uint8`, `(h, w, 3)`, JPEG만 허용, 디코딩 전에 헤더 크기 확인)
- Produces (테스트 도우미): `server.tests.helpers.jpeg_bytes(w=64, h=48, color=(255, 0, 0)) -> bytes`, `server.tests.helpers.jpeg_with_fake_size(w: int, h: int) -> bytes`

- [ ] **Step 1: 테스트 도우미와 실패하는 테스트 작성**

`server/tests/helpers.py`:
```python
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
```

`server/tests/test_imaging.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_imaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.imaging'`

- [ ] **Step 3: 구현**

`server/imaging.py`:
```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_imaging.py -v`
Expected: 10 passed

- [ ] **Step 5: 커밋**

```bash
git add server/imaging.py server/tests/helpers.py server/tests/test_imaging.py
git commit -m "이미지 디코딩: JPEG만, 디코딩 전 해상도 확인

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 자세 모델 래퍼

**Files:**
- Create: `server/pose.py`
- Test: `server/tests/test_pose.py`

**Interfaces:**
- Produces: `server.pose.PoseModel(weights: str = "yolo11n-pose.pt", imgsz: int = 640, conf: float = 0.5)` — 생성 시 모델을 올리고 예열. `PoseModel.predict(img_bgr: np.ndarray) -> dict` = `{"img_w": int, "img_h": int, "infer_ms": float, "people": [{"box": [x1, y1, x2, y2], "score": float, "kpts": [[x, y, conf] × 17]}]}`, 좌표는 0~1로 잘라 둔다.
- 이 `predict`의 반환 형식이 Task 5·6의 `Predictor = Callable[[np.ndarray], dict]` 계약이다.

- [ ] **Step 1: 실패하는 테스트 작성** (실제 GPU 모델 사용)

`server/tests/test_pose.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_pose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.pose'`

- [ ] **Step 3: 구현**

`server/pose.py`:
```python
"""YOLO11n-pose 래퍼. 결과를 JSON으로 보낼 수 있는 0~1 비율 좌표로 바꾼다.

ultralytics 모델은 스레드 안전하지 않다. 호출은 app.py의 단일 실행기에서만 한다.
"""
import numpy as np
from ultralytics import YOLO


def _unit(v: float) -> float:
    return round(min(max(float(v), 0.0), 1.0), 4)


class PoseModel:
    def __init__(self, weights: str = "yolo11n-pose.pt", imgsz: int = 640, conf: float = 0.5):
        self.model = YOLO(weights)  # 가중치가 없으면 첫 실행 때 자동으로 내려받는다
        self.imgsz = imgsz
        self.conf = conf
        self.predict(np.zeros((480, 640, 3), np.uint8))  # 예열

    def predict(self, img_bgr: np.ndarray) -> dict:
        r = self.model.predict(img_bgr, imgsz=self.imgsz, conf=self.conf, classes=[0],
                               verbose=False, save=False)[0]
        h, w = r.orig_shape
        people = []
        if r.boxes is not None and len(r.boxes) and r.keypoints is not None:
            boxes = r.boxes.xyxyn.cpu().numpy()
            scores = r.boxes.conf.cpu().numpy()
            kxy = r.keypoints.xyn.cpu().numpy()
            kconf = (r.keypoints.conf.cpu().numpy() if r.keypoints.conf is not None
                     else np.ones(kxy.shape[:2], np.float32))
            for box, score, pts, confs in zip(boxes, scores, kxy, kconf):
                people.append({
                    "box": [_unit(v) for v in box],
                    "score": round(float(score), 3),
                    "kpts": [[_unit(x), _unit(y), round(float(c), 3)] for (x, y), c in zip(pts, confs)],
                })
        return {"img_w": int(w), "img_h": int(h),
                "infer_ms": round(float(r.speed["inference"]), 1), "people": people}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_pose.py -v`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
git add server/pose.py server/tests/test_pose.py
git commit -m "자세 모델 래퍼: 0~1 비율 좌표, 예열

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 연결 관리 (대기 연결 밀어내기, 인증 연결 대체)

**Files:**
- Create: `server/sessions.py`
- Test: `server/tests/test_sessions.py`

**Interfaces:**
- Produces: `server.sessions.NotPending(Exception)`, `server.sessions.Registry(max_pending: int)` —
  `add_pending(conn) -> list` (넘친 만큼 가장 오래된 대기 연결을 빼서 돌려줌),
  `promote(conn) -> object | None` (대기에서 빼고 활성으로, 이전 활성 연결을 돌려줌. **대기 목록에 없는 연결(밀려났거나 이미 활성)이면 아무것도 바꾸지 않고 `NotPending`**),
  `discard(conn) -> None` (그 연결이 가진 기록만 지움 — 다른 연결이 활성이면 건드리지 않음),
  속성 `active`, `pending`(deque)

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_sessions.py`:
```python
import pytest

from server.sessions import NotPending, Registry


def test_pending_overflow_evicts_oldest():
    reg = Registry(max_pending=2)
    assert reg.add_pending("a") == []
    assert reg.add_pending("b") == []
    assert reg.add_pending("c") == ["a"]
    assert list(reg.pending) == ["b", "c"]


def test_promote_returns_previous_active():
    reg = Registry(max_pending=4)
    reg.add_pending("a")
    assert reg.promote("a") is None
    reg.add_pending("b")
    assert reg.promote("b") == "a"
    assert reg.active == "b" and list(reg.pending) == []


def test_evicted_conn_cannot_become_active():
    # 밀려난 연결의 버퍼에 남은 인증 메시지가 나중에 처리돼도 정상 활성 연결을 빼앗지 못한다
    reg = Registry(max_pending=1)
    reg.add_pending("live")
    reg.promote("live")
    reg.add_pending("a")
    assert reg.add_pending("b") == ["a"]
    with pytest.raises(NotPending):
        reg.promote("a")
    assert reg.active == "live"


def test_promote_twice_is_refused():
    reg = Registry(max_pending=4)
    reg.add_pending("a")
    reg.promote("a")
    with pytest.raises(NotPending):
        reg.promote("a")
    assert reg.active == "a"


def test_discard_of_replaced_conn_keeps_new_active():
    reg = Registry(max_pending=4)
    for c in ("a", "b"):
        reg.add_pending(c)
        reg.promote(c)
    reg.discard("a")          # 대체된 a의 정리 코드가 늦게 돈다
    assert reg.active == "b"  # b의 기록은 남아야 한다
    reg.add_pending("c")
    assert reg.promote("c") == "b"


def test_discard_removes_pending_and_active():
    reg = Registry(max_pending=4)
    reg.add_pending("a")
    reg.discard("a")
    assert list(reg.pending) == []
    reg.add_pending("b")
    reg.promote("b")
    reg.discard("b")
    assert reg.active is None
    reg.discard("zzz")  # 모르는 연결도 오류 없이
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.sessions'`

- [ ] **Step 3: 구현**

`server/sessions.py`:
```python
"""연결 기록. 인증 연결은 1개(새 연결이 대체), 인증 대기 연결은 max_pending개(넘치면 오래된 것부터 밀어냄)."""
from collections import deque


class NotPending(Exception):
    """대기 목록에 없는 연결(이미 밀려났거나 이미 활성)을 활성으로 올리려 함."""


class Registry:
    def __init__(self, max_pending: int):
        self.max_pending = max_pending
        self.pending: deque = deque()
        self.active = None

    def add_pending(self, conn) -> list:
        self.pending.append(conn)
        evicted = []
        while len(self.pending) > self.max_pending:
            evicted.append(self.pending.popleft())
        return evicted

    def promote(self, conn):
        if conn not in self.pending:
            raise NotPending
        self.pending.remove(conn)
        previous = self.active
        self.active = conn
        return previous

    def discard(self, conn) -> None:
        if conn in self.pending:
            self.pending.remove(conn)
        if self.active is conn:
            self.active = None
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_sessions.py -v`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add server/sessions.py server/tests/test_sessions.py
git commit -m "연결 관리: 대기 연결 밀어내기, 인증 연결 대체, 밀려난 연결 승격 거부

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: WebSocket 접속·인증

**Files:**
- Create: `server/app.py`, `server/tests/conftest.py`
- Test: `server/tests/test_ws_auth.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `decode_jpeg`·`BadImage` (Task 2), `Registry`·`NotPending` (Task 4)
- Produces: `server.app.create_app(settings: Settings, predictor: Predictor) -> FastAPI`, `server.app.Predictor = Callable[[np.ndarray], dict]` (Task 3 `PoseModel.predict` 형식), 종료 코드 상수 `CLOSE_BAD_TOKEN=4001`, `CLOSE_BAD_ORIGIN=4003`, `CLOSE_AUTH_TIMEOUT=4008`, `CLOSE_EVICTED=4009`, `CLOSE_REPLACED=4010`
- Produces (테스트 픽스처): `make_client(predictor=fake_predictor, **settings_overrides) -> TestClient`, 도우미 `authed(client, **kw)` 컨텍스트 매니저, 상수 `TOKEN`, `ORIGIN`, 함수 `fake_predictor(img) -> dict`

- [ ] **Step 1: 픽스처와 실패하는 테스트 작성**

`server/tests/conftest.py`:
```python
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
```

`server/tests/test_ws_auth.py`:
```python
from contextlib import ExitStack

import pytest
from starlette.websockets import WebSocketDisconnect

from server.tests.conftest import ORIGIN, TOKEN, authed


def closed_with(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    return exc.value.code


def test_good_token_gets_ready(make_client):
    with make_client() as client, authed(client):
        pass


def test_allowed_origin_and_missing_origin_both_ok(make_client):
    with make_client() as client:
        with authed(client, headers={"origin": ORIGIN}):
            pass
        with authed(client):  # Origin 헤더 없음 → 토큰으로만 판단
            pass


def test_foreign_origin_closed_4003(make_client):
    with make_client() as client:
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            assert closed_with(ws) == 4003


@pytest.mark.parametrize("first", [
    {"type": "auth", "token": "wrong"},
    {"type": "auth", "token": TOKEN[:-1]},
    {"type": "auth"},
    {"type": "frame", "seq": 1},
])
def test_bad_auth_closed_4001(make_client, first):
    with make_client() as client, client.websocket_connect("/ws") as ws:
        ws.send_json(first)
        assert closed_with(ws) == 4001


def test_non_json_or_binary_before_auth_closed_4001(make_client):
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hello")
            assert closed_with(ws) == 4001
        with client.websocket_connect("/ws") as ws:
            ws.send_bytes(b"\xff\xd8")
            assert closed_with(ws) == 4001


def test_auth_timeout_closed_4008(make_client):
    with make_client(auth_timeout=0.2) as client, client.websocket_connect("/ws") as ws:
        assert closed_with(ws) == 4008


def test_pending_overflow_evicts_oldest_4009(make_client):
    with make_client(auth_timeout=5) as client, ExitStack() as stack:
        oldest = stack.enter_context(client.websocket_connect("/ws"))
        for _ in range(3):
            stack.enter_context(client.websocket_connect("/ws"))
        with authed(client):  # 5번째 연결은 정상 인증된다
            assert closed_with(oldest) == 4009


def test_new_auth_replaces_old_4010(make_client):
    with make_client() as client, authed(client) as a:
        with authed(client):
            assert closed_with(a) == 4010


def test_third_connection_replaces_second_after_first_cleanup(make_client):
    with make_client() as client:
        with authed(client) as a:
            b_ctx = authed(client)
            b = b_ctx.__enter__()
            assert closed_with(a) == 4010
        # a의 정리 코드가 끝난 뒤에도 b가 활성으로 남아 있어야 c가 b를 밀어낸다
        with authed(client):
            assert closed_with(b) == 4010
        b_ctx.__exit__(None, None, None)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_ws_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.app'`

- [ ] **Step 3: 구현**

`server/app.py`:
```python
"""WebSocket 자세 인식 백엔드. 설계: design/2026-09-29-web-pose-design.md 4~6장."""
import asyncio
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable

import numpy as np
from fastapi import FastAPI, WebSocket

from .config import Settings, load_settings
from .imaging import BadImage, decode_jpeg
from .sessions import NotPending, Registry

Predictor = Callable[[np.ndarray], dict]
log = logging.getLogger("pose")

CLOSE_BAD_TOKEN = 4001
CLOSE_BAD_ORIGIN = 4003
CLOSE_AUTH_TIMEOUT = 4008
CLOSE_EVICTED = 4009
CLOSE_REPLACED = 4010


async def _close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass  # 이미 닫힌 연결


async def _send(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False  # 닫힌 연결로 보내다 실패하면 조용히 끝낸다


def create_app(settings: Settings, predictor: Predictor) -> FastAPI:
    # 모델 호출이 겹치지 않도록 추론 전용 스레드는 하나만 둔다 (연결이 대체되는 순간 포함)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pose")
    registry = Registry(settings.max_pending)
    token = settings.token.encode()

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    async def authenticate(ws: WebSocket) -> bool:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=settings.auth_timeout)
        except asyncio.TimeoutError:
            await _close(ws, CLOSE_AUTH_TIMEOUT)
            return False
        if msg["type"] == "websocket.disconnect":
            return False
        try:
            data = json.loads(msg.get("text") or "")
        except ValueError:
            data = None
        given = data.get("token") if isinstance(data, dict) and data.get("type") == "auth" else None
        if not isinstance(given, str) or not secrets.compare_digest(given.encode(), token):
            await _close(ws, CLOSE_BAD_TOKEN)
            return False
        return True

    async def serve_frames(ws: WebSocket) -> None:
        # Task 6에서 프레임 처리로 바꾼다. 지금은 모든 메시지에 bad_message로 답한다.
        while (await ws.receive())["type"] != "websocket.disconnect":
            if not await _send(ws, {"type": "error", "code": "bad_message"}):
                return

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()  # 닫기 코드를 브라우저에 전하려면 먼저 수락해야 한다
        origin = ws.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in settings.allowed_origins:
            await _close(ws, CLOSE_BAD_ORIGIN)
            return
        for old in registry.add_pending(ws):
            await _close(old, CLOSE_EVICTED)
        try:
            if not await authenticate(ws):
                return
            try:
                previous = registry.promote(ws)
            except NotPending:
                # 이미 4009로 밀려난 연결이 버퍼에 남은 인증 메시지로 통과한 경우: 활성 연결을 건드리지 않는다
                await _close(ws, CLOSE_EVICTED)
                return
            if previous is not None:
                await _close(previous, CLOSE_REPLACED)
            if await _send(ws, {"type": "ready", "model": settings.model_name}):
                await serve_frames(ws)
        finally:
            registry.discard(ws)

    return app
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_ws_auth.py -v`
Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app.py server/tests/conftest.py server/tests/test_ws_auth.py
git commit -m "WebSocket 접속·인증: Origin, 토큰, 대기 제한, 연결 대체

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 프레임 처리

**Files:**
- Modify: `server/app.py` (`serve_frames` 교체, `_frame_seq`·`process` 추가)
- Test: `server/tests/test_ws_frames.py`

**Interfaces:**
- Consumes: `create_app`, 픽스처 `make_client`·`authed` (Task 5), `jpeg_bytes`·`jpeg_with_fake_size` (Task 2)
- Produces: 설계 5장 메시지 규격 — 응답 `{"type":"result","seq","img_w","img_h","infer_ms","people"}` / `{"type":"error","code":"too_large"|"bad_image"|"server_error","seq"}` / `{"type":"error","code":"bad_message"}`

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_ws_frames.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_ws_frames.py -v`
Expected: 3개 PASS, 나머지 FAIL.
- PASS: `test_health_and_no_docs`(Task 5에서 구현), `test_binary_without_frame_is_bad_message`, `test_bad_message_discards_pending_frame` — 임시 `serve_frames`가 모든 메시지에 `bad_message`로 답해 우연히 기대값과 같다.
- FAIL: 나머지 — `result`·`too_large`·`bad_image`·`server_error` 대신 `bad_message`가 온다.

- [ ] **Step 3: 구현**

`server/app.py` — `_send` 아래에 추가:
```python
def _frame_seq(text: str) -> int | None:
    """{"type":"frame","seq":N}이면 N(0 이상 정수), 아니면 None."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "frame":
        return None
    seq = data.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return None
    return seq
```

`create_app` 안 `authenticate` 위에 추가:
```python
    def process(data: bytes) -> dict:
        return predictor(decode_jpeg(data, settings.max_side))
```

`serve_frames`를 다음으로 교체:
```python
    async def serve_frames(ws: WebSocket) -> None:
        loop = asyncio.get_running_loop()
        pending_seq = None
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            text, data = msg.get("text"), msg.get("bytes")
            if text is not None:
                seq = _frame_seq(text)
                if seq is not None:
                    pending_seq = seq  # frame이 연속으로 오면 나중 것을 쓴다
                    continue
                pending_seq = None
                if not await _send(ws, {"type": "error", "code": "bad_message"}):
                    return
                continue
            if data is None or pending_seq is None:
                if not await _send(ws, {"type": "error", "code": "bad_message"}):
                    return
                continue
            seq, pending_seq = pending_seq, None
            if len(data) > settings.max_bytes:
                reply = {"type": "error", "code": "too_large", "seq": seq}
            else:
                try:
                    result = await loop.run_in_executor(executor, process, data)
                    reply = {"type": "result", "seq": seq, **result}
                except BadImage:
                    reply = {"type": "error", "code": "bad_image", "seq": seq}
                except Exception as exc:  # GPU 메모리 부족 등. 연결은 유지한다
                    log.warning("추론 실패: %s", type(exc).__name__)  # 예외 종류만, 이미지·메시지 없이
                    reply = {"type": "error", "code": "server_error", "seq": seq}
            if not await _send(ws, reply):
                return
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests -v --ignore=server/tests/test_pose.py`
Expected: 모두 PASS (test_ws_frames 18개 포함)

- [ ] **Step 5: 커밋**

```bash
git add server/app.py server/tests/test_ws_frames.py
git commit -m "프레임 처리: seq, too_large·bad_image·bad_message·server_error, 추론 단일 실행기

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 실제 서버 조립, 통합 테스트, 실행 스크립트, 스트림 도구

**Files:**
- Modify: `server/app.py` (끝에 `build_app` 추가)
- Create: `server/run.ps1`, `tools/stream_video.py`
- Test: `server/tests/test_integration.py`

**Interfaces:**
- Consumes: `create_app` (Task 5·6), `PoseModel` (Task 3), `load_settings` (Task 1)
- Produces: `server.app.build_app() -> FastAPI` (`uvicorn --factory server.app:build_app` 진입점), `tools/stream_video.py --url WS_URL [--video PATH] [--frames N] [--token T] [--origin O]`

- [ ] **Step 1: 실패하는 통합 테스트 작성** (실제 uvicorn + 실제 모델)

`server/tests/test_integration.py`:
```python
"""실제 uvicorn을 띄워 TestClient가 거치지 않는 부분(--ws-max-size, 실제 모델)을 검증한다."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from ultralytics.utils import ASSETS
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

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


def authed(base):
    ws = connect(f"ws://{base}/ws", open_timeout=10)
    ws.send(json.dumps({"type": "auth", "token": TOKEN}))
    assert json.loads(ws.recv(timeout=10)) == {"type": "ready", "model": "yolo11n-pose"}
    return ws


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
    for p in r["people"]:
        assert len(p["kpts"]) == 17
        assert all(0 <= v <= 1 for x, y, _ in p["kpts"] for v in (x, y))


def test_over_ws_max_size_disconnects(server):
    with authed(server) as ws:
        ws.send(json.dumps({"type": "frame", "seq": 1}))
        # 서버는 헤더만 보고 바로 닫으므로 send에서 먼저 끊김(Windows는 RST)이 날 수 있다
        with pytest.raises((ConnectionClosed, OSError)):
            ws.send(b"\xff" * 2_200_000)
            ws.recv(timeout=10)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python -m pytest server/tests/test_integration.py -v`
Expected: FAIL — 서버가 `Error loading ASGI app factory: ... has no attribute 'build_app'`로 종료, "서버가 종료됨"

- [ ] **Step 3: 구현**

`server/app.py` 끝에 추가:
```python
def build_app() -> FastAPI:
    """uvicorn --factory server.app:build_app 진입점. 실제 모델을 올린다."""
    from .pose import PoseModel  # 단위 테스트가 GPU 모델을 불러오지 않도록 여기서 가져온다

    settings = load_settings()
    return create_app(settings, PoseModel(settings.model).predict)
```

`server/run.ps1`:
```powershell
# 백엔드 실행: Funnel 켜기 → 서버 실행 → 종료(Ctrl+C) 시 Funnel 끄기
# 사용: powershell -ExecutionPolicy Bypass -File server\run.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Ts = "C:\Program Files\Tailscale\tailscale.exe"

if (-not (Test-Path (Join-Path $PSScriptRoot ".env"))) {
    throw "server\.env가 없습니다. 먼저 실행: .venv\Scripts\python tools\gen_token.py"
}
Set-Location $Root

# 출력을 숨기지 않는다: Funnel·HTTPS가 아직 허용되지 않았으면 허용 링크를 출력하고 기다린다.
# 이미 켜져 있으면 같은 설정으로 다시 적용된다.
& $Ts funnel --bg 8000
if ($LASTEXITCODE -ne 0) { throw "Funnel을 켜지 못했습니다. 'tailscale funnel status'를 확인하세요." }

try {
    & $Py -m uvicorn --factory server.app:build_app --host 127.0.0.1 --port 8000 `
        --ws-max-size 2097152 --log-level warning
}
finally {
    & $Ts funnel --https=443 off | Out-Null
    Write-Host "Funnel 꺼짐"
}
```

`tools/stream_video.py`:
```python
"""동영상(없으면 예시 사진 반복)을 /ws로 흘려보내 카메라 없이 전체 경로를 검증한다.

사용:
  .venv\\Scripts\\python tools/stream_video.py --url ws://127.0.0.1:8000/ws
  .venv\\Scripts\\python tools/stream_video.py --url wss://<pc이름>.<tailnet>.ts.net/ws --video 파일.mp4
토큰은 server/.env의 TOKEN을 쓴다 (--token으로 바꿀 수 있음).
"""
import argparse
import json
import time
from pathlib import Path

import cv2
from dotenv import dotenv_values
from ultralytics.utils import ASSETS
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]


def frames(video: str | None, n: int):
    if video is None:
        img = cv2.imread(str(ASSETS / "bus.jpg"))
        for _ in range(n):
            yield img
        return
    cap = cv2.VideoCapture(video)
    try:
        for _ in range(n):
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def to_jpeg(img, max_side: int = 640, quality: int = 70) -> bytes:
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="ws://…/ws 또는 wss://…/ws")
    ap.add_argument("--video", help="동영상 파일 (없으면 예시 사진 반복)")
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--token")
    ap.add_argument("--origin", help="Origin 헤더 (예: https://lumos0107.github.io)")
    args = ap.parse_args()

    token = args.token or dotenv_values(ROOT / "server" / ".env").get("TOKEN")
    if not token:
        raise SystemExit("토큰이 없습니다: server/.env 또는 --token")

    done = errors = people = 0
    infer = 0.0
    try:
        with connect(args.url, origin=args.origin, open_timeout=20) as ws:
            ws.send(json.dumps({"type": "auth", "token": token.strip()}))
            ready = json.loads(ws.recv(timeout=10))
            start = time.perf_counter()
            for seq, img in enumerate(frames(args.video, args.frames)):
                ws.send(json.dumps({"type": "frame", "seq": seq}))
                ws.send(to_jpeg(img))
                r = json.loads(ws.recv(timeout=10))
                if r.get("seq") != seq:
                    raise SystemExit(f"seq 불일치: 보냄 {seq}, 받음 {r}")
                if r["type"] == "result":
                    done += 1
                    infer += r["infer_ms"]
                    people += len(r["people"])
                else:
                    errors += 1
            elapsed = time.perf_counter() - start
    except ConnectionClosed as exc:
        code = exc.rcvd.code if exc.rcvd else "-"
        raise SystemExit(f"서버가 연결을 닫음 (코드 {code})")
    if done == 0:
        raise SystemExit(f"결과 0건 (오류 {errors})")
    print(f"모델 {ready['model']}  프레임 {done}  오류 {errors}  왕복 fps {done / elapsed:.1f}  "
          f"평균 추론 {infer / done:.1f}ms  평균 인원 {people / done:.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python -m pytest server/tests -v`
Expected: 전체 PASS (통합 3개 포함)

- [ ] **Step 5: 도구 수동 확인** (Funnel 없이 로컬)

Run (터미널 1): `.venv\Scripts\python tools/gen_token.py` → 토큰 출력, `server/.env` 생성
Run (터미널 1): `.venv\Scripts\python -m uvicorn --factory server.app:build_app --host 127.0.0.1 --port 8000 --ws-max-size 2097152 --log-level warning`
Run (터미널 2): `.venv\Scripts\python tools/stream_video.py --url ws://127.0.0.1:8000/ws --frames 100`
Expected: `프레임 100  오류 0 … 평균 인원 3.xx` 비슷한 한 줄. 끝나면 터미널 1 Ctrl+C.
`--origin https://evil.example`로 다시 실행 → `서버가 연결을 닫음 (코드 4003)`.

- [ ] **Step 6: 커밋**

```bash
git add server/app.py server/run.ps1 server/tests/test_integration.py tools/stream_video.py
git commit -m "실제 서버 조립, 통합 테스트, 실행 스크립트, 스트림 검증 도구

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 프런트 순수 함수

**Files:**
- Create: `lib.js`, `package.json`
- Test: `web_tests/lib.test.mjs`

**Interfaces:**
- Produces (`lib.js` ES 모듈):
  - `CLOSE = {BAD_TOKEN:4001, BAD_ORIGIN:4003, AUTH_TIMEOUT:4008, EVICTED:4009, REPLACED:4010}`
  - `STATUS = {IDLE:"대기", STOPPED:"정지", NEED_SETUP:"설정 필요", CONNECTING:"연결 중", CONNECTED:"연결됨", BAD_TOKEN:"토큰 확인", BAD_ORIGIN:"허용되지 않은 주소", REPLACED:"다른 기기에서 사용 중", OFFLINE:"서버 꺼짐"}`
  - `closePolicy(code: number) -> {retry: boolean, status: string}`
  - `retryDelayMs(attempt: number) -> number` (3000, 6000, 10000, 10000, …)
  - `normalizeServerUrl(input: string) -> string | null` (`ws:`는 `localhost`·`127.0.0.1`·`[::1]`만, 나머지 평문 주소는 `null`)
  - `cleanToken(input: string) -> string`
  - `fitContain(srcW, srcH, boxW, boxH) -> {x, y, w, h} | null`
  - `scaleToLongSide(w, h, maxSide = 640) -> {w, h}` (확대하지 않음)
  - `SKELETON: [number, number][]` (COCO 17점 연결 19개)
  - `toCanvas([nx, ny], rect) -> [x, y]`
  - `visibleSegments(kpts, rect, minConf) -> [x1, y1, x2, y2][]`, `visiblePoints(kpts, rect, minConf) -> [x, y][]`
  - `ema(prev: number | null, sample: number, alpha = 0.2) -> number`

- [ ] **Step 1: 실패하는 테스트 작성**

`package.json`:
```json
{
  "private": true,
  "type": "module",
  "scripts": { "test": "node --test web_tests/" }
}
```

`web_tests/lib.test.mjs`:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CLOSE, STATUS, closePolicy, retryDelayMs, normalizeServerUrl, cleanToken,
  fitContain, scaleToLongSide, SKELETON, toCanvas, visibleSegments, visiblePoints, ema,
} from "../lib.js";

test("재연결하지 않는 종료 코드", () => {
  assert.deepEqual(closePolicy(CLOSE.BAD_TOKEN), { retry: false, status: STATUS.BAD_TOKEN });
  assert.deepEqual(closePolicy(CLOSE.BAD_ORIGIN), { retry: false, status: STATUS.BAD_ORIGIN });
  assert.deepEqual(closePolicy(CLOSE.REPLACED), { retry: false, status: STATUS.REPLACED });
});

test("재연결하는 종료 코드", () => {
  for (const code of [CLOSE.AUTH_TIMEOUT, CLOSE.EVICTED, 1006, 1000, 1011, 1009]) {
    assert.deepEqual(closePolicy(code), { retry: true, status: STATUS.OFFLINE });
  }
});

test("재연결 간격 3초부터 최대 10초", () => {
  assert.deepEqual([0, 1, 2, 3, 9].map(retryDelayMs), [3000, 6000, 10000, 10000, 10000]);
});

test("서버 주소 정규화", () => {
  const cases = [
    ["mypc.tailnet.ts.net", "wss://mypc.tailnet.ts.net/ws"],
    ["https://mypc.tailnet.ts.net", "wss://mypc.tailnet.ts.net/ws"],
    ["https://mypc.tailnet.ts.net/", "wss://mypc.tailnet.ts.net/ws"],
    ["https://mypc.tailnet.ts.net/ws", "wss://mypc.tailnet.ts.net/ws"],
    ["  wss://h.ts.net/ws  ", "wss://h.ts.net/ws"],
    ["http://127.0.0.1:8000", "ws://127.0.0.1:8000/ws"],
    ["ws://localhost:8000/ws", "ws://localhost:8000/ws"],
    ["http://[::1]:8000", "ws://[::1]:8000/ws"],
  ];
  for (const [input, want] of cases) assert.equal(normalizeServerUrl(input), want, input);
  const bads = ["", "   ", "ftp://x.com", "http://", null, undefined,
    "http://mypc.tailnet.ts.net", "ws://example.com/ws", "http://192.168.0.10:8000"]; // 원격 평문 거부
  for (const bad of bads) {
    assert.equal(normalizeServerUrl(bad), null, String(bad));
  }
});

test("토큰 앞뒤 공백·줄바꿈 제거", () => {
  assert.equal(cleanToken("  abc-DEF_123\n"), "abc-DEF_123");
  assert.equal(cleanToken(undefined), "");
});

test("fitContain: 가로 영상을 세로 화면에", () => {
  assert.deepEqual(fitContain(640, 360, 360, 640), { x: 0, y: 218.75, w: 360, h: 202.5 });
});

test("fitContain: 세로 영상을 가로 화면에", () => {
  assert.deepEqual(fitContain(480, 640, 800, 600), { x: 175, y: 0, w: 450, h: 600 });
});

test("fitContain: 크기 0이면 null", () => {
  assert.equal(fitContain(0, 480, 100, 100), null);
  assert.equal(fitContain(640, 480, 0, 100), null);
});

test("긴 변 640으로 축소, 확대는 하지 않음", () => {
  assert.deepEqual(scaleToLongSide(1280, 720), { w: 640, h: 360 });
  assert.deepEqual(scaleToLongSide(720, 1280), { w: 360, h: 640 });
  assert.deepEqual(scaleToLongSide(320, 240), { w: 320, h: 240 });
});

test("뼈대 연결 19개, 인덱스 0~16", () => {
  assert.equal(SKELETON.length, 19);
  assert.ok(SKELETON.flat().every((i) => Number.isInteger(i) && i >= 0 && i < 17));
});

test("좌표 변환과 신뢰도 필터", () => {
  const rect = { x: 10, y: 20, w: 100, h: 200 };
  assert.deepEqual(toCanvas([0.5, 0.25], rect), [60, 70]);
  const kpts = Array.from({ length: 17 }, () => [0, 0, 0.1]);
  kpts[5] = [0.2, 0.3, 0.9];  // 왼쪽 어깨
  kpts[6] = [0.4, 0.3, 0.8];  // 오른쪽 어깨
  kpts[7] = [0.2, 0.5, 0.4];  // 왼쪽 팔꿈치 (신뢰도 낮음)
  assert.deepEqual(visibleSegments(kpts, rect, 0.5), [[30, 80, 50, 80]]);
  assert.deepEqual(visiblePoints(kpts, rect, 0.5), [[30, 80], [50, 80]]);
});

test("ema", () => {
  assert.equal(ema(null, 10), 10);
  assert.equal(ema(10, 20, 0.5), 15);
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test web_tests/`
Expected: FAIL — `Cannot find module …/lib.js`

- [ ] **Step 3: 구현**

`lib.js`:
```js
// 화면과 무관한 순수 함수 — app.js가 쓰고 web_tests/lib.test.mjs가 검증한다.

export const CLOSE = { BAD_TOKEN: 4001, BAD_ORIGIN: 4003, AUTH_TIMEOUT: 4008, EVICTED: 4009, REPLACED: 4010 };

export const STATUS = {
  IDLE: "대기",
  STOPPED: "정지",
  NEED_SETUP: "설정 필요",
  CONNECTING: "연결 중",
  CONNECTED: "연결됨",
  BAD_TOKEN: "토큰 확인",
  BAD_ORIGIN: "허용되지 않은 주소",
  REPLACED: "다른 기기에서 사용 중",
  OFFLINE: "서버 꺼짐",
};

// 4001·4003·4010은 다시 붙어도 같은 결과(또는 두 탭이 서로 뺏는 반복)라 재연결하지 않는다.
export function closePolicy(code) {
  if (code === CLOSE.BAD_TOKEN) return { retry: false, status: STATUS.BAD_TOKEN };
  if (code === CLOSE.BAD_ORIGIN) return { retry: false, status: STATUS.BAD_ORIGIN };
  if (code === CLOSE.REPLACED) return { retry: false, status: STATUS.REPLACED };
  return { retry: true, status: STATUS.OFFLINE };
}

export function retryDelayMs(attempt) {
  return Math.min(3000 * 2 ** attempt, 10000);
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

// 호스트만, https 주소, /ws가 붙은 주소 모두 받아 WebSocket 주소로 바꾼다.
// 암호화되지 않은 http/ws는 이 PC 안(localhost)일 때만 허용한다 — 토큰이 평문으로 원격에 가지 않게.
export function normalizeServerUrl(input) {
  let s = String(input ?? "").trim();
  if (!s) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `https://${s}`;
  let url;
  try {
    url = new URL(s);
  } catch {
    return null;
  }
  const scheme = { "https:": "wss:", "wss:": "wss:", "http:": "ws:", "ws:": "ws:" }[url.protocol];
  if (!scheme || !url.host) return null;
  if (scheme === "ws:" && !LOCAL_HOSTS.has(url.hostname)) return null;
  return `${scheme}//${url.host}/ws`;
}

export function cleanToken(input) {
  return String(input ?? "").trim();
}

// object-fit: contain으로 표시된 영상이 상자 안에서 차지하는 영역
export function fitContain(srcW, srcH, boxW, boxH) {
  if (!(srcW > 0 && srcH > 0 && boxW > 0 && boxH > 0)) return null;
  const scale = Math.min(boxW / srcW, boxH / srcH);
  const w = srcW * scale;
  const h = srcH * scale;
  return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h };
}

export function scaleToLongSide(w, h, maxSide = 640) {
  const scale = Math.min(1, maxSide / Math.max(w, h));
  return { w: Math.round(w * scale), h: Math.round(h * scale) };
}

// COCO 17점: 0 코, 1·2 눈, 3·4 귀, 5·6 어깨, 7·8 팔꿈치, 9·10 손목, 11·12 골반, 13·14 무릎, 15·16 발목
export const SKELETON = [
  [15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12], [5, 6], [5, 7], [6, 8],
  [7, 9], [8, 10], [1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6],
];

export function toCanvas([nx, ny], rect) {
  return [rect.x + nx * rect.w, rect.y + ny * rect.h];
}

export function visibleSegments(kpts, rect, minConf) {
  const out = [];
  for (const [a, b] of SKELETON) {
    if (kpts[a][2] >= minConf && kpts[b][2] >= minConf) {
      out.push([...toCanvas(kpts[a], rect), ...toCanvas(kpts[b], rect)]);
    }
  }
  return out;
}

export function visiblePoints(kpts, rect, minConf) {
  return kpts.filter((k) => k[2] >= minConf).map((k) => toCanvas(k, rect));
}

export function ema(prev, sample, alpha = 0.2) {
  return prev == null ? sample : prev + alpha * (sample - prev);
}
```

- [ ] **Step 4: 통과 확인**

Run: `node --test web_tests/`
Expected: 12 tests pass, 0 fail

- [ ] **Step 5: 커밋**

```bash
git add lib.js package.json web_tests/lib.test.mjs
git commit -m "프런트 순수 함수: 종료 코드 판단, 주소 정규화, 좌표 변환

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 프런트 화면

**Files:**
- Create: `index.html`, `style.css`, `app.js`, `.nojekyll`
- Test: Task 10의 브라우저 검증이 이 화면을 검증한다. 여기서는 문법 확인과 로컬 수동 확인.

**Interfaces:**
- Consumes: `lib.js` 전부 (Task 8)
- Produces (Task 10이 쓰는 DOM): `#status`(상태 문구, `data-kind` = `ok`|`wait`|`bad`), `#fps`, `#ms`, `#people`(인원 숫자), `#notice`, `#start`(문구 `시작`/`정지`), `#flip`, `#mode`(문구 `표시: 실시간`/`표시: 동기`), `#settings-btn`, `dialog#settings` 안 `#server-url`, `#token`, `button[value=save]`, `button[value=cancel]`, `#settings-error`, `#stage`(`.mirror`, `.sync` 클래스), `video#video`, `canvas#overlay`
- `localStorage` 키: `pose.serverUrl`, `pose.token`

- [ ] **Step 1: 화면 골격**

`.nojekyll`: 빈 파일 (Pages가 Jekyll 변환 없이 파일을 그대로 제공).

`index.html`:
```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'self'; connect-src wss: ws://localhost:* ws://127.0.0.1:*; img-src 'self' blob:; media-src 'self' blob:">
  <title>눈길손길 자세 인식 시제품</title>
  <link rel="stylesheet" href="style.css">
  <script type="module" src="app.js"></script>
</head>
<body>
  <header class="bar">
    <span id="status" data-kind="wait">대기</span>
    <span class="stat">fps <b id="fps">-</b></span>
    <span class="stat">처리 <b id="ms">-</b>ms</span>
    <span class="stat">인원 <b id="people">-</b></span>
  </header>

  <main id="stage" class="stage">
    <video id="video" playsinline muted autoplay></video>
    <canvas id="overlay"></canvas>
  </main>

  <p id="notice" class="notice" role="status"></p>
  <p class="hint">실시간 표시에서는 뼈대가 조금 늦게 따라옵니다. 정확히 겹쳐 보려면 표시 방식을 '동기'로 바꾸세요. 영상은 저장되지 않습니다.</p>

  <footer class="controls">
    <button id="start" class="primary">시작</button>
    <button id="flip">카메라 전환</button>
    <button id="mode">표시: 실시간</button>
    <button id="settings-btn">설정</button>
  </footer>

  <dialog id="settings">
    <form id="settings-form" method="dialog">
      <h2>서버 설정</h2>
      <label>서버 주소
        <input id="server-url" type="text" inputmode="url" autocomplete="off" spellcheck="false"
               placeholder="https://&lt;pc이름&gt;.&lt;tailnet&gt;.ts.net">
      </label>
      <label>토큰
        <input id="token" type="password" autocomplete="off" spellcheck="false">
      </label>
      <p class="small">이 브라우저에만 저장됩니다.</p>
      <p id="settings-error" class="error"></p>
      <menu>
        <button value="cancel" formnovalidate>취소</button>
        <button value="save" class="primary">저장</button>
      </menu>
    </form>
  </dialog>
</body>
</html>
```

`style.css`:
```css
:root {
  --bg: #0f1115; --panel: #1a1d24; --text: #e8eaed; --muted: #9aa0a6;
  --ok: #34d399; --wait: #fbbf24; --bad: #f87171; --accent: #5170ff;
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  display: flex; flex-direction: column; height: 100dvh;
  background: var(--bg); color: var(--text);
  font: 15px/1.4 system-ui, "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
}
.bar {
  display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center;
  padding: 10px 12px; padding-top: max(10px, env(safe-area-inset-top));
  background: var(--panel);
}
#status { font-weight: 700; padding: 2px 10px; border-radius: 999px; background: #2a2e37; }
#status[data-kind="ok"] { color: var(--ok); }
#status[data-kind="wait"] { color: var(--wait); }
#status[data-kind="bad"] { color: var(--bad); }
.stat { color: var(--muted); font-variant-numeric: tabular-nums; }
.stat b { color: var(--text); }
.stage { position: relative; flex: 1; min-height: 0; background: #000; overflow: hidden; }
.stage video, .stage canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
.stage video { object-fit: contain; }
.stage.mirror { transform: scaleX(-1); }       /* 영상과 캔버스를 함께 뒤집는다 */
.stage.sync video { visibility: hidden; }      /* 동기 표시: 보낸 프레임을 캔버스에 그린다 */
.notice { margin: 8px 12px 0; min-height: 1.4em; color: var(--wait); }
.notice:empty { display: none; }
.hint { margin: 6px 12px; color: var(--muted); font-size: 13px; }
.controls {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
  padding: 10px 12px; padding-bottom: max(10px, env(safe-area-inset-bottom));
  background: var(--panel);
}
button {
  font: inherit; color: var(--text); background: #2a2e37;
  border: 0; border-radius: 10px; padding: 12px 6px; min-height: 44px;
}
button.primary { background: var(--accent); color: #fff; font-weight: 700; }
dialog {
  width: min(92vw, 420px); border: 0; border-radius: 14px;
  background: var(--panel); color: var(--text); padding: 18px;
}
dialog::backdrop { background: rgb(0 0 0 / 0.6); }
dialog h2 { margin: 0 0 12px; font-size: 18px; }
dialog label { display: block; margin-bottom: 12px; color: var(--muted); }
dialog input {
  display: block; width: 100%; margin-top: 4px; padding: 10px;
  font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid #3a3f4b; border-radius: 8px;
}
dialog menu { display: flex; gap: 8px; justify-content: flex-end; padding: 0; margin: 12px 0 0; }
dialog menu button { padding: 10px 18px; }
.small { font-size: 13px; color: var(--muted); margin: 0; }
.error { color: var(--bad); margin: 6px 0 0; min-height: 1.2em; }
```

- [ ] **Step 2: 동작 코드**

`app.js`:
```js
import {
  STATUS, closePolicy, retryDelayMs, normalizeServerUrl, cleanToken,
  fitContain, scaleToLongSide, visibleSegments, visiblePoints, ema,
} from "./lib.js";

const JPEG_QUALITY = 0.7;
const MAX_SIDE = 640;
const REPLY_TIMEOUT_MS = 5000;
const KPT_MIN_CONF = 0.5;

const $ = (id) => document.getElementById(id);
const video = $("video");
const stage = $("stage");
const overlay = $("overlay");
const ctx = overlay.getContext("2d");
const capture = document.createElement("canvas"); // 전송할 프레임
const cctx = capture.getContext("2d");
const shown = document.createElement("canvas");   // 마지막 결과를 만든 프레임 (동기 표시용)
const sctx = shown.getContext("2d");
const KEY_URL = "pose.serverUrl";
const KEY_TOKEN = "pose.token";

const state = {
  running: false, facing: "environment", mode: "live",
  ws: null, authed: false, waitingSeq: null, seq: 0, attempt: 0,
  replyTimer: null, retryTimer: null,
  lastResult: null, lastResultAt: null, fps: null,
  stream: null, wakeLock: null,
};

// ---------- 설정 (이 브라우저에만 저장) ----------
function loadSettings() {
  try {
    return { url: localStorage.getItem(KEY_URL) || "", token: localStorage.getItem(KEY_TOKEN) || "" };
  } catch {
    return { url: "", token: "" };
  }
}
function saveSettings(url, token) {
  try {
    localStorage.setItem(KEY_URL, url);
    localStorage.setItem(KEY_TOKEN, token);
  } catch {
    setNotice("브라우저 저장소를 쓸 수 없어 설정이 저장되지 않았습니다.");
  }
}
function hasSettings() {
  const s = loadSettings();
  return Boolean(normalizeServerUrl(s.url) && s.token);
}

// ---------- 표시 ----------
function setStatus(text, kind) {
  $("status").textContent = text;
  $("status").dataset.kind = kind;
}
function setNotice(text) {
  $("notice").textContent = text || "";
}
function resetStats() {
  state.fps = null;
  state.lastResultAt = null;
  $("fps").textContent = "-";
  $("ms").textContent = "-";
  $("people").textContent = "-";
}

function draw() {
  const bw = stage.clientWidth;
  const bh = stage.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (overlay.width !== Math.round(bw * dpr) || overlay.height !== Math.round(bh * dpr)) {
    overlay.width = Math.round(bw * dpr);
    overlay.height = Math.round(bh * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, bw, bh);
  const r = state.lastResult;
  const rect = fitContain(video.videoWidth, video.videoHeight, bw, bh);
  if (!r || !rect) return;
  if (state.mode === "sync") ctx.drawImage(shown, rect.x, rect.y, rect.w, rect.h);
  for (const p of r.people) {
    const [x1, y1, x2, y2] = p.box;
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2;
    ctx.strokeRect(rect.x + x1 * rect.w, rect.y + y1 * rect.h, (x2 - x1) * rect.w, (y2 - y1) * rect.h);
    ctx.strokeStyle = "#facc15";
    ctx.lineWidth = 3;
    for (const [ax, ay, bx, by] of visibleSegments(p.kpts, rect, KPT_MIN_CONF)) {
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.fillStyle = "#f43f5e";
    for (const [x, y] of visiblePoints(p.kpts, rect, KPT_MIN_CONF)) {
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function clearOverlay() {
  state.lastResult = null;
  draw();
}

// ---------- 카메라 ----------
async function startCamera() {
  stopCamera();
  if (!navigator.mediaDevices?.getUserMedia) {
    setNotice("이 브라우저(또는 https가 아닌 주소)에서는 카메라를 쓸 수 없습니다.");
    return false;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: state.facing, width: { ideal: 1280 }, height: { ideal: 720 } },
    });
  } catch (e) {
    setNotice(
      e.name === "NotAllowedError"
        ? "카메라 권한이 거부됐습니다. 주소창 왼쪽의 사이트 설정(자물쇠)에서 카메라를 허용한 뒤 새로고침하세요."
        : e.name === "NotFoundError"
          ? "카메라를 찾을 수 없습니다."
          : `카메라를 켤 수 없습니다 (${e.name}).`,
    );
    return false;
  }
  video.srcObject = state.stream;
  await video.play().catch(() => {});
  // 요청한 방향이 아니라 실제로 잡힌 카메라로 판단한다. 값이 없으면(노트북 웹캠 등) 전면으로 본다.
  const facing = state.stream.getVideoTracks()[0]?.getSettings().facingMode;
  stage.classList.toggle("mirror", (facing || "user") === "user");
  return true;
}

function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  video.srcObject = null;
}

// ---------- 화면 꺼짐 방지 ----------
async function requestWakeLock() {
  try {
    state.wakeLock = (await navigator.wakeLock?.request("screen")) ?? null;
  } catch {
    state.wakeLock = null; // 지원하지 않거나 거부되면 무시
  }
}
function releaseWakeLock() {
  state.wakeLock?.release().catch(() => {});
  state.wakeLock = null;
}

// ---------- 연결 ----------
function detachSocket() {
  const ws = state.ws;
  state.ws = null;
  state.authed = false;
  state.waitingSeq = null;
  clearTimeout(state.replyTimer);
  if (ws) {
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(1000); } catch { /* 이미 닫힘 */ }
  }
}

function connect() {
  clearTimeout(state.retryTimer);
  const { url, token } = loadSettings();
  const wsUrl = normalizeServerUrl(url);
  if (!wsUrl || !token) {
    stop();
    setStatus(STATUS.NEED_SETUP, "bad");
    openSettings();
    return;
  }
  setStatus(STATUS.CONNECTING, "wait");
  let ws;
  try {
    ws = new WebSocket(wsUrl);
  } catch {
    stop();
    setStatus(STATUS.NEED_SETUP, "bad");
    setNotice("서버 주소로 연결할 수 없습니다. 설정을 확인하세요.");
    return;
  }
  state.ws = ws;
  state.authed = false;
  state.waitingSeq = null;
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
  ws.onmessage = (ev) => onMessage(ws, ev);
  ws.onclose = (ev) => onClose(ws, ev);
}

function scheduleReconnect() {
  const delay = retryDelayMs(state.attempt++);
  setNotice(`${delay / 1000}초 뒤 다시 연결합니다.`);
  clearTimeout(state.retryTimer);
  state.retryTimer = setTimeout(connect, delay);
}

function onMessage(ws, ev) {
  if (ws !== state.ws || typeof ev.data !== "string") return;
  let msg;
  try {
    msg = JSON.parse(ev.data);
  } catch {
    return;
  }
  if (msg.type === "ready") {
    state.authed = true;
    state.attempt = 0;
    setStatus(STATUS.CONNECTED, "ok");
    setNotice("");
    pump();
    return;
  }
  if (msg.type !== "result" && msg.type !== "error") return;
  if (msg.seq !== undefined && msg.seq !== state.waitingSeq) return; // 이전 프레임의 늦은 응답
  clearTimeout(state.replyTimer);
  state.waitingSeq = null;
  if (msg.type === "result") onResult(msg);
  else setNotice(`프레임 오류: ${msg.code}`);
  pump();
}

function onClose(ws, ev) {
  if (ws !== state.ws) return;
  state.ws = null;
  state.authed = false;
  state.waitingSeq = null;
  clearTimeout(state.replyTimer);
  clearOverlay();
  const policy = closePolicy(ev.code);
  if (!state.running) return;
  if (policy.retry) {
    setStatus(policy.status, "wait");
    scheduleReconnect();
  } else {
    stop();
    setStatus(policy.status, "bad");
    setNotice(policy.status === STATUS.BAD_TOKEN ? "토큰이 맞지 않습니다. 설정에서 고친 뒤 다시 시작하세요." : "");
  }
}

// 응답이 5초 안에 오지 않으면 연결이 멈춘 것으로 보고 새로 붙는다 (와이파이↔LTE 전환 등)
function onReplyTimeout() {
  detachSocket();
  clearOverlay();
  setStatus(STATUS.OFFLINE, "wait");
  scheduleReconnect();
}

// ---------- 프레임 전송 (한 장 보내고 결과를 받은 뒤 다음 장) ----------
async function pump() {
  if (!state.running || !state.authed || state.waitingSeq !== null || document.hidden) return;
  if (!video.videoWidth) {
    setTimeout(pump, 100);
    return;
  }
  const { w, h } = scaleToLongSide(video.videoWidth, video.videoHeight, MAX_SIDE);
  capture.width = w;
  capture.height = h;
  cctx.drawImage(video, 0, 0, w, h);
  const seq = ++state.seq;
  state.waitingSeq = seq;
  const blob = await new Promise((resolve) => capture.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
  const ws = state.ws;
  if (state.waitingSeq !== seq) return;
  if (!blob || !ws || ws.readyState !== WebSocket.OPEN) {
    state.waitingSeq = null;
    return;
  }
  ws.send(JSON.stringify({ type: "frame", seq }));
  ws.send(blob);
  state.replyTimer = setTimeout(onReplyTimeout, REPLY_TIMEOUT_MS);
}

function onResult(r) {
  const now = performance.now();
  if (state.lastResultAt !== null) state.fps = ema(state.fps, 1000 / Math.max(1, now - state.lastResultAt));
  state.lastResultAt = now;
  state.lastResult = r;
  // 다음 pump가 capture를 덮어쓰기 전에 이 결과의 프레임을 보관한다 (동기 표시 재그리기용)
  shown.width = capture.width;
  shown.height = capture.height;
  sctx.drawImage(capture, 0, 0);
  setNotice(""); // 앞선 프레임 오류 안내 지우기
  $("fps").textContent = state.fps == null ? "-" : state.fps.toFixed(1);
  $("ms").textContent = r.infer_ms.toFixed(1);
  $("people").textContent = String(r.people.length);
  draw();
}

// ---------- 시작·정지 ----------
async function start() {
  if (!hasSettings()) {
    setStatus(STATUS.NEED_SETUP, "bad");
    openSettings();
    return;
  }
  setNotice("");
  if (!(await startCamera())) return;
  state.running = true;
  state.attempt = 0;
  $("start").textContent = "정지";
  resetStats();
  requestWakeLock();
  connect();
}

function stop() {
  state.running = false;
  $("start").textContent = "시작";
  clearTimeout(state.retryTimer);
  detachSocket();
  stopCamera();
  releaseWakeLock();
  clearOverlay();
  setStatus(STATUS.STOPPED, "wait");
}

// ---------- 설정 창 ----------
function openSettings() {
  const s = loadSettings();
  $("server-url").value = s.url;
  $("token").value = s.token;
  $("settings-error").textContent = "";
  if (!$("settings").open) $("settings").showModal();
}

$("settings-form").addEventListener("submit", (e) => {
  if (e.submitter?.value !== "save") return;
  const url = $("server-url").value.trim();
  const token = cleanToken($("token").value);
  if (!normalizeServerUrl(url)) {
    e.preventDefault();
    $("settings-error").textContent = "서버 주소 형식이 올바르지 않습니다.";
    return;
  }
  if (!token) {
    e.preventDefault();
    $("settings-error").textContent = "토큰을 입력하세요.";
    return;
  }
  saveSettings(url, token);
  if (state.running) {
    detachSocket();
    state.attempt = 0;
    connect();
  } else {
    setStatus(STATUS.IDLE, "wait");
  }
});

// ---------- 버튼·이벤트 ----------
$("start").addEventListener("click", () => (state.running ? stop() : start()));
$("settings-btn").addEventListener("click", openSettings);

$("flip").addEventListener("click", async () => {
  state.facing = state.facing === "user" ? "environment" : "user";
  if (state.running && !(await startCamera())) stop();
});

$("mode").addEventListener("click", () => {
  state.mode = state.mode === "live" ? "sync" : "live";
  $("mode").textContent = state.mode === "live" ? "표시: 실시간" : "표시: 동기";
  stage.classList.toggle("sync", state.mode === "sync");
  draw();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.running) {
    requestWakeLock(); // 탭이 가려지면 브라우저가 해제하므로 다시 요청
    pump();
  }
});

window.addEventListener("resize", draw);

setStatus(hasSettings() ? STATUS.IDLE : STATUS.NEED_SETUP, hasSettings() ? "wait" : "bad");
```

- [ ] **Step 3: 문법 확인**

Run: `node --check app.js && node --check lib.js && node --test web_tests/`
Expected: 출력 없이 통과, 테스트 12 pass

- [ ] **Step 4: 로컬 수동 확인** (카메라 없는 PC라 화면 표시까지만)

Run (터미널 1): `.venv\Scripts\python -m http.server 5500 --bind 127.0.0.1`
브라우저로 `http://localhost:5500/` 열기
Expected: 상태 `설정 필요`(빨강), 네 버튼, `설정` 누르면 대화상자. 잘못된 주소 `ftp://x` 저장 → "서버 주소 형식이 올바르지 않습니다." 브라우저 콘솔에 오류 없음.

- [ ] **Step 5: 커밋**

```bash
git add index.html style.css app.js .nojekyll
git commit -m "프런트 화면: 카메라, 전송, 오버레이, 재연결, 설정

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: 브라우저 종단 검증 (가짜 카메라)

**Files:**
- Create: `tools/e2e_browser.py`

**Interfaces:**
- Consumes: Task 9 DOM 계약, `build_app` (Task 7)
- Produces: `.venv\Scripts\python tools/e2e_browser.py [--shots DIR]` — 성공 시 종료 코드 0과 단계별 `OK` 출력, 실패 시 0이 아닌 코드

- [ ] **Step 1: 검증 스크립트 작성**

`tools/e2e_browser.py`:
```python
"""브라우저 종단 검증: 예시 사진을 가짜 카메라로 넣고 로컬 페이지 → 로컬 백엔드 전체를 확인한다.

사용: .venv\\Scripts\\python tools/e2e_browser.py [--shots 스크린샷폴더]
설치된 Chrome을 쓴다 (playwright 브라우저 내려받기 불필요). 스크린샷은 저장소 밖에 둔다.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import cv2
from playwright.sync_api import expect, sync_playwright
from ultralytics.utils import ASSETS

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "e" * 43
PAGE_PORT, API_PORT = 5500, 8765
PAGE = f"http://localhost:{PAGE_PORT}/"


def write_y4m(dst: Path, w: int = 480, h: int = 640, frames: int = 30) -> None:
    """세로(480x640) 가짜 카메라 영상. 폰을 세운 상황을 흉내낸다."""
    img = cv2.resize(cv2.imread(str(ASSETS / "bus.jpg")), (w, h))
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
    with open(dst, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F30:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(frames):
            f.write(b"FRAME\n" + yuv)


def start_api() -> subprocess.Popen:
    env = {**os.environ, "TOKEN": TOKEN, "ALLOWED_ORIGINS": f"http://localhost:{PAGE_PORT}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "server.app:build_app", "--host", "127.0.0.1",
         "--port", str(API_PORT), "--ws-max-size", "2097152", "--log-level", "warning"],
        cwd=ROOT, env=env)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("백엔드가 뜨지 않음")


def stop_proc(proc: subprocess.Popen) -> None:
    proc.terminate()
    proc.wait(timeout=15)


def step(name: str) -> None:
    print(f"OK  {name}", flush=True)


def people(page) -> int:
    text = page.locator("#people").text_content() or "-"
    return int(text) if text.isdigit() else -1


def configure(page, token: str) -> None:
    page.click("#settings-btn")
    page.fill("#server-url", f"http://127.0.0.1:{API_PORT}")
    page.fill("#token", token)
    page.click("#settings button[value=save]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path, default=Path(tempfile.gettempdir()) / "e2e_shots")
    args = ap.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp())
    y4m = tmp / "cam.y4m"
    write_y4m(y4m)
    web = subprocess.Popen([sys.executable, "-m", "http.server", str(PAGE_PORT), "--bind", "127.0.0.1"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = start_api()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True, args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-video-capture={y4m}",
            ])
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(PAGE)
            status = page.locator("#status")
            expect(status).to_have_text("설정 필요")
            step("설정 없으면 '설정 필요'")

            configure(page, f"  {TOKEN}  ")  # 앞뒤 공백이 붙은 토큰
            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            page.wait_for_function("Number(document.getElementById('people').textContent) >= 3",
                                   timeout=30_000)
            page.screenshot(path=str(args.shots / "1_live.png"))
            step(f"공백 붙은 토큰으로 연결, 세로 영상에서 {people(page)}명 인식")

            page.click("#mode")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(args.shots / "2_sync.png"))
            expect(page.locator("#stage")).to_have_class(re.compile(r"\bsync\b"))
            page.click("#mode")
            step("동기 표시 전환")

            page2 = context.new_page()
            page2.goto(PAGE)
            page2.click("#start")
            expect(page2.locator("#status")).to_have_text("연결됨", timeout=30_000)
            expect(status).to_have_text("다른 기기에서 사용 중", timeout=10_000)
            page.wait_for_timeout(5000)
            expect(status).to_have_text("다른 기기에서 사용 중")
            expect(page2.locator("#status")).to_have_text("연결됨")
            expect(page.locator("#start")).to_have_text("시작")
            page2.close()
            step("다른 탭이 가져가면 '다른 기기에서 사용 중', 5초간 서로 뺏지 않음")

            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            stop_proc(api)
            expect(status).to_have_text("서버 꺼짐", timeout=15_000)
            api = start_api()
            expect(status).to_have_text("연결됨", timeout=60_000)
            page.wait_for_function("Number(document.getElementById('people').textContent) >= 3",
                                   timeout=30_000)
            step("서버가 죽었다 살아나면 새로고침 없이 다시 연결")

            configure(page, "wrong-token")
            expect(status).to_have_text("토큰 확인", timeout=15_000)
            page.wait_for_timeout(5000)
            expect(status).to_have_text("토큰 확인")
            expect(page.locator("#start")).to_have_text("시작")
            step("틀린 토큰이면 '토큰 확인', 재연결하지 않음")

            if errors:
                raise SystemExit(f"페이지 오류: {errors}")
            browser.close()
    finally:
        for proc in (api, web):
            if proc.poll() is None:
                stop_proc(proc)
    print(f"전체 통과. 스크린샷: {args.shots}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행**

Run: `.venv\Scripts\python tools/e2e_browser.py --shots <스크래치패드>/e2e_shots`
Expected: `OK` 6줄과 `전체 통과`. 실패하면 superpowers:systematic-debugging으로 원인을 찾고 `app.js`·서버를 고친다 (검증 스크립트의 기대값을 낮춰 통과시키지 않는다).

- [ ] **Step 3: 스크린샷 확인**

`1_live.png`, `2_sync.png`를 열어 본다.
Expected: 세로 영상이 가운데 있고 좌우에 검은 여백. 사람 3명 이상에 박스와 노란 뼈대가 **사람 위에 정확히 겹침**. 동기 화면도 같은 위치. 가짜 카메라는 방향 정보가 없어 전면으로 간주되므로 영상이 좌우 반전돼 보일 수 있는데, 이때도 뼈대가 함께 반전돼 겹쳐야 한다 (반전 좌표 검증).

- [ ] **Step 4: 커밋**

```bash
git add tools/e2e_browser.py
git commit -m "브라우저 종단 검증: 가짜 카메라, 연결 대체, 서버 재시작, 틀린 토큰

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: 배포와 실제 경로 검증

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README 사용법**

`README.md`:
````markdown
# safety-for-old-man

팀 눈길손길 (제주대 창의융합 캡스톤디자인) — 다중 센서 융합 비전 기반 독거노인 안전 모니터링 시스템의 **개발용 자세 인식 시제품**.

폰·노트북 브라우저 카메라 영상을 개발 PC의 GPU로 보내 관절 17개를 인식하고 화면에 뼈대를 그린다.
원본 영상을 네트워크로 보내는 **개발용 구조**이며, 최종 시스템(엣지 처리·원본 영상 미전송)과 다르다.
영상은 저장하지 않는다. 촬영 전 대상자 동의를 받는다.

- 페이지: https://lumos0107.github.io/safety-for-old-man/
- 설계: [design/2026-09-29-web-pose-design.md](design/2026-09-29-web-pose-design.md)

## 서버 (개발 PC)

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv -r server/requirements.txt
.venv\Scripts\python tools\gen_token.py          # server/.env 생성, 토큰 출력
powershell -ExecutionPolicy Bypass -File server\run.ps1   # Funnel 켜고 서버 실행, Ctrl+C로 둘 다 끔
```

창을 그냥 닫았다면 `tailscale funnel status`로 확인하고 `tailscale funnel --https=443 off`.
토큰이 새었다면 `tools\gen_token.py --force` 후 서버 재시작.

## 사용 (폰·노트북)

페이지 → 설정에 서버 주소(`https://<pc이름>.<tailnet>.ts.net`)와 토큰 입력 → 시작.

## 테스트

```powershell
.venv\Scripts\python -m pytest            # 서버 (GPU 필요)
node --test web_tests/                    # 프런트 순수 함수
.venv\Scripts\python tools\e2e_browser.py # 브라우저 종단 (Chrome 필요)
```
````

- [ ] **Step 2: 전체 검증 후 커밋**

Run: `.venv\Scripts\python -m pytest && node --test web_tests/ && .venv\Scripts\python tools/e2e_browser.py`
Expected: 모두 통과

Run: `git status --short` 와 `git ls-files` 로 저장소에 올라갈 파일 확인
Expected: `.env`, `*.pt`, `.venv`, 이미지·영상 파일이 목록에 없음.
실제 호스트 문자열로 검사한다 (tailnet 이름 형식에 기대지 않는다. 주소를 계획·코드에 적지 않도록 실행 시점에 읽는다). 주소는 CT 로그로 어차피 공개되므로 이 검사는 보안 수단이 아니라 저장소 정리 목적이다.
Run (PowerShell):
```powershell
$h = (& "C:\Program Files\Tailscale\tailscale.exe" status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
$pc = $h.Split('.')[0]; $tn = $h.Split('.')[1]
git grep -n -e $pc -e $tn; git log --all -p | Select-String -SimpleMatch -Pattern $pc, $tn | Measure-Object | % Count
```
Expected: `git grep` 출력 없음, 개수 `0`.

```bash
git add README.md
git commit -m "README: 실행·사용·테스트 방법

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Pages 배포 설정 확인**

저장소는 public이고 Pages가 이미 켜져 있다 (2026-09-29 확인: `main` 브랜치의 "pages build and deployment" 성공, 사이트 루트 `200`). 배포 폴더가 `(root)`인지는 push 뒤 Step 4에서 `app.js`가 `200`인지로 확인한다.
`404`이면 사용자에게 저장소 Settings → Pages → Source `Deploy from a branch`, `main` / `(root)`로 바꿔 달라고 요청한 뒤 다시 확인한다.

- [ ] **Step 4: push와 Pages 확인** (첫 push 때 사용자가 GitHub 로그인)

Run: `git push -u origin main`
Expected: push 성공. 1~2분 뒤 `curl -s -o /dev/null -w "%{http_code}" https://lumos0107.github.io/safety-for-old-man/app.js` → `200`, 페이지 HTML에 `눈길손길 자세 인식 시제품`.

- [ ] **Step 5: Funnel 경로 검증**

Run (터미널 1): `powershell -ExecutionPolicy Bypass -File server\run.ps1`
Expected: `Available on the internet: https://<pc이름>.<tailnet>.ts.net/ … proxy http://127.0.0.1:8000` 뒤 서버 대기

Run (터미널 2): `.venv\Scripts\python tools/stream_video.py --url wss://<실제 Funnel 주소>/ws --origin https://lumos0107.github.io --frames 100`
Expected: `프레임 100  오류 0 …` 한 줄

Run: `curl -s -o /dev/null -w "%{http_code}" https://<실제 Funnel 주소>/docs` → `404`

터미널 1 Ctrl+C → `Funnel 꺼짐` 출력, `tailscale funnel status` → `No serve config`

- [ ] **Step 6: 사용자 폰 시험** (사용자)

`run.ps1` 실행 상태에서 폰으로 Pages 접속 → 설정(서버 주소·토큰) → 시작 → 뼈대 표시, 동기 표시, 카메라 전환 확인. 와이파이를 끄고 LTE로 바꿔 `서버 꺼짐` → `연결됨`으로 돌아오는지 확인.
````

---

## 계획 검토 반영 기록 (`2026-09-29-web-pose-plan-review.md`)

모든 항목을 반영했다. 다르게 반영한 것:

- **2.1 밀려난 연결의 인증 경쟁**: 검토안의 app 한 줄(`ws not in registry.pending`) 대신 `Registry.promote`가 대기 목록에 없는 연결을 `NotPending`으로 거부한다. 효과는 같고, 비동기 타이밍을 재현하지 않아도 Task 4 단위 테스트로 확정적으로 검증된다. app은 이 경우 해당 연결만 `4009`로 닫는다.
- **2.4 Pages 활성화**: 이미 `main` 브랜치에서 배포 중이라(2026-09-29 확인) 설정 단계 대신 확인 단계로 넣었다 (Task 11 Step 3).
- **2.5 주소 유출 검사**: 실제 호스트 문자열로 검사하되, 계획·코드에 주소를 적지 않도록 실행 시점에 `tailscale status --json`에서 읽는다.
- **3.5 CSP**: 선택 항목이지만 반영했다. Task 10 브라우저 검증이 CSP 때문에 연결이 막히지 않는지 함께 확인한다.
- **5장 반전 판단**: 가짜 카메라는 방향 정보가 없어 전면으로 간주되므로, Task 10 스크린샷이 반전 좌표까지 검증하게 됐다.
