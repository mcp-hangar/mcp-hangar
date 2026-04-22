"""Tests for the domain lock contract."""

import threading
from types import TracebackType

from mcp_hangar.domain.contracts import ILock
from mcp_hangar.domain.repository import InMemoryMcpServerRepository


def test_threading_lock_satisfies_lock_protocol():
    """threading.Lock should satisfy the domain lock protocol."""
    lock = threading.Lock()

    assert isinstance(lock, ILock)


class StubLock:
    """Minimal test lock that satisfies the domain lock protocol."""

    def __init__(self) -> None:
        self.enter_calls = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return True

    def release(self) -> None:
        return None

    def __enter__(self) -> bool:
        self.enter_calls += 1
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def test_repository_uses_injected_lock_factory():
    """Repository should create its lock through the injected factory."""
    lock = StubLock()

    repository = InMemoryMcpServerRepository(lock_factory=lambda: lock)
    repository.add("provider-1", object())

    assert lock.enter_calls == 1
