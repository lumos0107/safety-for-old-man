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


@pytest.mark.parametrize("raw", [
    "[" * 20000,                                   # 깊게 중첩된 JSON (RecursionError)
    '{"type": "auth", "token": "\ud800"}',        # 짝 없는 서로게이트 (UnicodeEncodeError)
    '{"type": "auth", "token": "' + "x" * 5000 + '"}',  # 지나치게 긴 인증 메시지
], ids=["deep_nesting", "lone_surrogate", "too_long"])
def test_malformed_auth_closed_4001_without_traceback(make_client, raw, caplog):
    with make_client() as client, client.websocket_connect("/ws") as ws:
        ws.send_text(raw)
        assert closed_with(ws) == 4001


def test_auth_events_are_logged_without_token(make_client, caplog):
    caplog.set_level("WARNING", logger="pose")
    with make_client() as client:
        with authed(client):
            with authed(client):
                pass
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": "wrong"})
            assert closed_with(ws) == 4001
    text = caplog.text
    assert "인증 성공" in text and "이전 연결 대체" in text and "인증 실패" in text
    assert TOKEN not in text and "wrong" not in text


def test_ready_message_logged_on_startup(make_client, caplog):
    caplog.set_level("WARNING", logger="pose")
    with make_client():
        pass
    assert "서버 준비 완료" in caplog.text
