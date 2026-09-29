import time
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


def test_auth_failure_burst_is_summarized(make_client, caplog, monkeypatch):
    import server.app as app_module
    monkeypatch.setattr(app_module, "FAIL_SUMMARY_SEC", 0.3)
    caplog.set_level("WARNING", logger="pose")
    with make_client() as client:
        for _ in range(5):
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "auth", "token": "wrong"})
                assert closed_with(ws) == 4001
        time.sleep(0.8)  # 요약 창이 닫히기를 기다린다 (다음 실패가 없어도 요약이 나와야 한다)
    lines = [r.getMessage() for r in caplog.records if "인증 실패" in r.getMessage()]
    assert len(lines) == 2, lines  # 첫 실패 즉시 1줄 + 나머지 4건 요약 1줄
    assert "4건" in lines[1]
    assert all("gen_token" not in l and "토큰은 안전" in l for l in lines)  # 틀린 토큰은 유출 신호가 아니다


def test_foreign_origin_logged_escaped_and_summarized(make_client, caplog, monkeypatch):
    import server.app as app_module
    monkeypatch.setattr(app_module, "FAIL_SUMMARY_SEC", 0.3)
    caplog.set_level("WARNING", logger="pose")
    with make_client() as client:
        for _ in range(3):
            # 헤더 값은 바이트로 보낸다 (문자열이면 클라이언트가 비ASCII를 거부) — 서버에서는 latin-1로 풀려 \x85가 된다
            with client.websocket_connect("/ws", headers={"origin": b"https://evil.example\x85x"}) as ws:
                assert closed_with(ws) == 4003
        time.sleep(0.8)
    lines = [r.getMessage() for r in caplog.records if "허용되지 않은 주소" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "2건" in lines[1]
    assert all("\x85" not in l for l in lines)  # 제어문자가 그대로 찍히지 않는다
    assert "\\x85" in lines[0]  # 이스케이프된 글자로 보인다


def test_replacement_log_mentions_same_device_reconnect(make_client, caplog):
    caplog.set_level("WARNING", logger="pose")
    with make_client() as client, authed(client):
        with authed(client):
            pass
    assert "같은 기기의 재접속이면 정상" in caplog.text
    assert "밀려난 이전 기기" in caplog.text  # 안내가 뜨는 곳은 새 기기가 아니라 밀려난 쪽


def test_printable_escapes_control_and_non_ascii():
    from server.app import _printable
    raw = "a" + chr(0x1b) + chr(0x7f) + chr(0xac00) + chr(0x85) + chr(9)
    assert _printable(raw) == "a\\x1b\\x7f\\uac00\\x85\\t"
    assert _printable("x" * 200) == "x" * 80


def test_origin_refusal_hint_says_restart_and_unknown_stays(make_client, caplog):
    caplog.set_level("WARNING", logger="pose")
    with make_client() as client, client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
        assert closed_with(ws) == 4003
    assert "서버 재시작" in caplog.text and "모르는 주소면 그대로" in caplog.text
