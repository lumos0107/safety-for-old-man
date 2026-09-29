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

    def process(data: bytes) -> dict:
        return predictor(decode_jpeg(data, settings.max_side))

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
