"""Lock hierarchy management for thread safety.

This module provides a formal lock ordering system to prevent deadlocks.
All locks in the system should be acquired in ascending order of their level.

Lock Hierarchy (acquire in ascending order):
    Level 10-19: Domain aggregates (Provider, ProviderGroup)
    Level 20-29: Infrastructure buses (EventBus, CommandBus, QueryBus)
    Level 30-39: Persistence (EventStore, Repository)
    Level 40-49: Orchestration (SagaManager)
    Level 50-59: Client I/O (StdioClient, HttpClient) - always last

Rule: Never acquire a lock at level N while holding a lock at level >= N.

Example valid acquisition order:
    Provider._lock (10) -> EventBus._lock (20) -> StdioClient.pending_lock (50)

Example INVALID (would deadlock):
    StdioClient.pending_lock (50) -> Provider._lock (10)  # WRONG!
"""

from enum import IntEnum
import threading
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)


class LockLevel(IntEnum):
    """Lock hierarchy levels - always acquire in ascending order.

    Lower numbers = higher priority = acquire first.
    Higher numbers = lower priority = acquire last.
    """

    # Level 1x: Domain aggregates (highest priority - acquire first)
    PROVIDER = 10
    PROVIDER_GROUP = 11

    # Level 2x: Infrastructure buses
    EVENT_BUS = 20
    COMMAND_BUS = 21
    QUERY_BUS = 22

    # Level 3x: Persistence layer
    EVENT_STORE = 30
    REPOSITORY = 31
    CONFIG_REPOSITORY = 32
    AUDIT_REPOSITORY = 33

    # Level 4x: Orchestration
    SAGA_MANAGER = 40

    # Level 5x: Client I/O (lowest priority - acquire last)
    STDIO_CLIENT = 50
    HTTP_CLIENT = 51
    OBSERVABILITY = 52


class LockOrderViolation(Exception):
    """Raised when lock acquisition order is violated."""

    def __init__(self, message: str, current_level: int, requested_level: int, held_locks: list[str]):
        self.current_level = current_level
        self.requested_level = requested_level
        self.held_locks = held_locks
        super().__init__(message)


# Thread-local storage for tracking held locks
_thread_locks: threading.local = threading.local()


def _get_held_locks() -> list[tuple[int, str]]:
    """Get list of (level, name) for locks held by current thread."""
    if not hasattr(_thread_locks, "held"):
        _thread_locks.held = []
    return _thread_locks.held


def _register_lock(level: int, name: str) -> None:
    """Register that current thread acquired a lock."""
    held = _get_held_locks()
    held.append((level, name))


def _unregister_lock(level: int, name: str) -> None:
    """Unregister that current thread released a lock."""
    held = _get_held_locks()
    # Remove last occurrence (LIFO order)
    for i in range(len(held) - 1, -1, -1):
        if held[i] == (level, name):
            held.pop(i)
            break


def _check_lock_order(level: int, name: str, reentrant: bool = False) -> None:
    """Check if acquiring this lock would violate hierarchy.

    Args:
        level: Lock hierarchy level.
        name: Lock name for error messages.
        reentrant: If True, re-acquiring the same lock is allowed (RLock behavior).

    Raises:
        LockOrderViolation: If lock order would be violated.
    """
    held = _get_held_locks()
    if not held:
        return

    # For reentrant locks, allow re-acquiring the same lock
    if reentrant:
        for held_level, held_name in held:
            if held_level == level and held_name == name:
                # Same lock being re-acquired - OK for RLock
                return

    max_held_level = max(h[0] for h in held)
    if max_held_level >= level:
        held_names = [h[1] for h in held if h[0] >= level]
        raise LockOrderViolation(
            f"Lock order violation: cannot acquire '{name}' (level {level}) "
            f"while holding locks at level >= {level}: {held_names}",
            current_level=max_held_level,
            requested_level=level,
            held_locks=held_names,
        )


class TrackedLock:
    """Lock wrapper with hierarchy tracking for deadlock prevention.

    In debug mode (__debug__ = True, i.e., not running with -O flag),
    this lock validates that acquisition order follows the hierarchy.

    In optimized mode (-O flag), tracking is disabled for performance.

    Usage:
        # In class __init__:
        # Lock hierarchy level: PROVIDER (10)
        # Safe to acquire after: (none - top level)
        # Safe to acquire before: EVENT_BUS, EVENT_STORE, STDIO_CLIENT
        self._lock = TrackedLock(LockLevel.PROVIDER, f"Provider:{provider_id}")

        # Usage:
        with self._lock:
            # critical section
    """

    def __init__(self, level: LockLevel, name: str, reentrant: bool = True):
        """Initialize tracked lock.

        Args:
            level: Lock hierarchy level from LockLevel enum.
            name: Human-readable name for debugging (e.g., "Provider:math").
            reentrant: If True, use RLock (reentrant). If False, use Lock.
        """
        self._level = int(level)
        self._name = name
        self._lock: threading.RLock | threading.Lock = threading.RLock() if reentrant else threading.Lock()
        self._reentrant = reentrant

    @property
    def level(self) -> int:
        """Lock hierarchy level."""
        return self._level

    @property
    def name(self) -> str:
        """Lock name for debugging."""
        return self._name

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the lock with hierarchy validation.

        Args:
            blocking: If True, block until lock is acquired.
            timeout: Timeout in seconds (-1 for infinite).

        Returns:
            True if lock was acquired, False otherwise.

        Raises:
            LockOrderViolation: If acquisition would violate lock hierarchy (debug mode only).
        """
        if __debug__:
            _check_lock_order(self._level, self._name, self._reentrant)

        if timeout == -1:
            acquired = self._lock.acquire(blocking)
        else:
            acquired = self._lock.acquire(blocking, timeout)

        if acquired and __debug__:
            _register_lock(self._level, self._name)

        return acquired

    def release(self) -> None:
        """Release the lock."""
        if __debug__:
            _unregister_lock(self._level, self._name)
        self._lock.release()

    def __enter__(self) -> "TrackedLock":
        """Context manager entry - acquire lock."""
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Context manager exit - release lock."""
        self.release()
        return False

    def locked(self) -> bool:
        """Check if lock is currently held."""
        if hasattr(self._lock, "locked"):
            return self._lock.locked()
        # RLock doesn't have locked() in all Python versions
        acquired = self._lock.acquire(blocking=False)
        if acquired:
            self._lock.release()
            return False
        return True


class TrackedRLock(TrackedLock):
    """Convenience alias for reentrant TrackedLock."""

    def __init__(self, level: LockLevel, name: str):
        super().__init__(level, name, reentrant=True)


def get_current_thread_locks() -> list[tuple[int, str]]:
    """Get list of locks held by current thread (for debugging).

    Returns:
        List of (level, name) tuples for held locks.
    """
    return list(_get_held_locks())


def clear_thread_locks() -> None:
    """Clear lock tracking for current thread (for testing only)."""
    if hasattr(_thread_locks, "held"):
        _thread_locks.held.clear()
