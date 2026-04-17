"""Lock contract for synchronizing domain repository access."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable


@runtime_checkable
class ILock(Protocol):
    """Structural contract for lock implementations used by the domain."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the lock."""
        ...

    def release(self) -> None:
        """Release the lock."""
        ...

    def __enter__(self) -> bool:
        """Acquire the lock in a context manager."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lock when leaving a context manager."""
        ...
