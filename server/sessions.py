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
