"""WebSocket 자세 인식 백엔드. 설계: design/2026-09-29-web-pose-design.md 4~6장."""
import asyncio
import json
import logging
import secrets
import time
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
MAX_TEXT = 256  # 인증·frame 메시지는 60자 안팎 — 그보다 훨씬 긴 텍스트는 파싱하지 않는다 (깊은 중첩 JSON 방지)
FAIL_SUMMARY_SEC = 60  # 인증 실패·주소 거부는 스캐너가 콘솔을 도배하지 않게 1분 단위로 모은다


def _note(message: str) -> None:
    """서버 창에 시각과 함께 한 줄. 토큰·이미지·IP는 절대 넣지 않는다."""
    log.warning("%s %s", time.strftime("%H:%M:%S"), message)


def _printable(text: str, limit: int = 80) -> str:
    """상대가 보낸 문자열을 로그에 넣을 때: 길이 제한 + 비ASCII·제어문자 이스케이프."""
    return text[:limit].encode("ascii", "backslashreplace").decode("ascii")


class _Summarizer:
    """같은 종류의 사건을 모아 찍는다: 첫 건은 바로, 이어지는 건은 창(FAIL_SUMMARY_SEC)이 닫힐 때 한 줄.
    폭주가 이어지면 창을 다시 열어 창마다 한 줄, 조용해지면 멈춘다 (다음 사건을 기다리지 않고 제때 찍음)."""

    def __init__(self, first, more):
        self.first, self.more = first, more  # detail → 문구, (건수, 시작 시각, 최근 detail) → 문구
        self.count, self.since, self.last, self.handle = 0, "", "", None

    def hit(self, detail: str) -> None:
        if self.handle is None:
            _note(self.first(detail))
            self._open()
        else:
            self.count += 1
            self.last = detail

    def _open(self) -> None:
        self.count, self.since = 0, time.strftime("%H:%M:%S")
        self.handle = asyncio.get_running_loop().call_later(FAIL_SUMMARY_SEC, self._flush)

    def _flush(self) -> None:
        self.handle = None
        if self.count:
            _note(self.more(self.count, self.since, self.last))
            self._open()  # 폭주가 이어질 수 있으니 한 창 더 모은다

    def cancel(self) -> None:
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None


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


def _frame_seq(text: str) -> int | None:
    """{"type":"frame","seq":N}이면 N(0 이상 정수), 아니면 None."""
    if len(text) > MAX_TEXT:
        return None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("type") != "frame":
        return None
    seq = data.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return None
    return seq


def create_app(settings: Settings, predictor: Predictor) -> FastAPI:
    # 모델 호출이 겹치지 않도록 추론 전용 스레드는 하나만 둔다 (연결이 대체되는 순간 포함)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pose")
    registry = Registry(settings.max_pending)
    token = settings.token.encode()
    # 틀린 토큰은 상대가 토큰을 모른다는 뜻이다 — 유출 신호가 아니다 (README '서버 창 로그 읽는 법')
    auth_failures = _Summarizer(
        lambda why: f"인증 실패 ({why}) — 옛 토큰이나 스캐너, 토큰은 안전",
        lambda n, since, why: f"{since}부터 인증 실패 {n}건 더 (최근: {why}) — 토큰은 안전",
    )
    origin_refusals = _Summarizer(
        lambda o: f"허용되지 않은 주소에서 접속 거부: {o} (server/.env의 ALLOWED_ORIGINS 확인)",
        lambda n, since, o: f"{since}부터 허용되지 않은 주소 거부 {n}건 더 (최근: {o})",
    )
    note_failure = auth_failures.hit

    @asynccontextmanager
    async def lifespan(app):
        _note("서버 준비 완료 — 폰에서 시작하세요 (끄려면 이 창에서 Ctrl+C)")
        yield
        auth_failures.cancel()
        origin_refusals.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def process(data: bytes) -> dict:
        return predictor(decode_jpeg(data, settings.max_side))

    async def authenticate(ws: WebSocket) -> bool:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=settings.auth_timeout)
        except asyncio.TimeoutError:
            note_failure("시간 초과")
            await _close(ws, CLOSE_AUTH_TIMEOUT)
            return False
        if msg["type"] == "websocket.disconnect":
            return False
        text = msg.get("text") or ""
        ok = False
        if len(text) <= MAX_TEXT:
            try:
                data = json.loads(text)
                given = data.get("token") if isinstance(data, dict) and data.get("type") == "auth" else None
                # 짝 없는 서로게이트 같은 문자는 encode에서 UnicodeEncodeError(ValueError) — 틀린 토큰으로 본다
                ok = isinstance(given, str) and secrets.compare_digest(given.encode("utf-8"), token)
            except (ValueError, RecursionError):
                ok = False
        if not ok:
            note_failure("틀린 토큰")
            await _close(ws, CLOSE_BAD_TOKEN)
            return False
        return True

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

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()  # 닫기 코드를 브라우저에 전하려면 먼저 수락해야 한다
        origin = ws.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in settings.allowed_origins:
            origin_refusals.hit(_printable(origin))
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
                _note("인증 성공 — 이전 연결 대체 (같은 기기의 재접속이면 정상 / 다른 기기라면 그 기기에 '다른 기기에서 사용 중'이 뜸)")
                await _close(previous, CLOSE_REPLACED)
            else:
                _note("인증 성공 — 기기 연결됨")
            if await _send(ws, {"type": "ready", "model": settings.model_name}):
                await serve_frames(ws)
        finally:
            registry.discard(ws)

    return app


def build_app() -> FastAPI:
    """uvicorn --factory server.app:build_app 진입점. 실제 모델을 올린다."""
    from .pose import PoseModel  # 단위 테스트가 GPU 모델을 불러오지 않도록 여기서 가져온다

    settings = load_settings()
    return create_app(settings, PoseModel(settings.model).predict)
