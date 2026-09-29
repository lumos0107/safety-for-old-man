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
