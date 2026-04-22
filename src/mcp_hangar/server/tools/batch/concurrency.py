"""Concurrency management for batch execution.

Provides two-level semaphore-based concurrency control:
- Global semaphore: limits total in-flight calls across all mcp_servers
- Per-mcp_server semaphore: limits in-flight calls to each individual mcp_server

Both semaphores must be acquired before a call executes. Acquisition order
is always global-first, then mcp_server, to prevent deadlocks.

This module uses threading.Semaphore (not asyncio) because the batch executor
is thread-based by design. The semaphores are shared across batches, providing
cross-batch backpressure that ThreadPoolExecutor alone cannot achieve.

Example:
    manager = ConcurrencyManager(global_limit=50, default_mcp_server_limit=10)
    manager.set_mcp_server_limit("slow-api", 3)

    with manager.acquire("fast-api") as wait_time:
        # At most 50 calls globally, at most 10 to fast-api
        result = mcp_server.invoke_tool(...)
"""

from collections.abc import Generator
from contextlib import contextmanager
import threading
import time

from ....logging_config import get_logger
from ....metrics import (
    BATCH_CONCURRENCY_QUEUED_TOTAL,
    BATCH_CONCURRENCY_WAIT_SECONDS,
    BATCH_INFLIGHT_CALLS,
    BATCH_INFLIGHT_CALLS_PER_PROVIDER,
)

logger = get_logger(__name__)

# Default limits
DEFAULT_GLOBAL_CONCURRENCY = 50
DEFAULT_PROVIDER_CONCURRENCY = 10

# Sentinel for "unlimited" concurrency (0 or None in config)
UNLIMITED = 0


class ConcurrencyManager:
    """Two-level semaphore-based concurrency control.

    Manages a global semaphore and per-mcp_server semaphores. A call must
    acquire both before executing. Acquisition order is always global
    first, then mcp_server, to prevent deadlocks.

    The manager is designed to be shared across multiple BatchExecutor
    invocations (i.e., across concurrent hangar_call batches), providing
    system-wide backpressure.

    Attributes:
        global_limit: Maximum total in-flight calls (0 = unlimited).
        default_mcp_server_limit: Default per-mcp_server limit (0 = unlimited).
    """

    def __init__(
        self,
        global_limit: int = DEFAULT_GLOBAL_CONCURRENCY,
        default_mcp_server_limit: int = DEFAULT_PROVIDER_CONCURRENCY,
    ):
        """Initialize concurrency manager.

        Args:
            global_limit: Maximum total in-flight calls across all mcp_servers.
                Use 0 for unlimited.
            default_mcp_server_limit: Default per-mcp_server concurrency limit.
                Use 0 for unlimited. Can be overridden per mcp_server via
                set_mcp_server_limit().
        """
        if global_limit < 0:
            raise ValueError(f"global_limit must be >= 0, got {global_limit}")
        if default_mcp_server_limit < 0:
            raise ValueError(f"default_mcp_server_limit must be >= 0, got {default_mcp_server_limit}")

        self._global_limit = global_limit
        self._default_mcp_server_limit = default_mcp_server_limit

        # Global semaphore (None if unlimited)
        self._global_semaphore: threading.Semaphore | None = (
            threading.Semaphore(global_limit) if global_limit > 0 else None
        )

        # Per-mcp_server semaphores, created lazily
        self._mcp_server_semaphores: dict[str, threading.Semaphore | None] = {}
        self._mcp_server_limits: dict[str, int] = {}

        # Lock protects _mcp_server_semaphores and _mcp_server_limits dicts
        self._lock = threading.Lock()

        logger.info(
            "concurrency_manager_initialized",
            global_limit=global_limit if global_limit > 0 else "unlimited",
            default_mcp_server_limit=(default_mcp_server_limit if default_mcp_server_limit > 0 else "unlimited"),
        )

    @property
    def global_limit(self) -> int:
        """Global concurrency limit (0 = unlimited)."""
        return self._global_limit

    @property
    def default_mcp_server_limit(self) -> int:
        """Default per-mcp_server concurrency limit (0 = unlimited)."""
        return self._default_mcp_server_limit

    def set_mcp_server_limit(self, mcp_server_id: str, limit: int) -> None:
        """Set concurrency limit for a specific mcp_server.

        If called after the mcp_server's semaphore has been lazily created,
        replaces it with a new semaphore at the new limit. Existing
        in-flight calls on the old semaphore will complete normally.

        Args:
            mcp_server_id: McpServer identifier.
            limit: Maximum concurrent calls for this mcp_server (0 = unlimited).

        Raises:
            ValueError: If limit is negative.
        """
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")

        with self._lock:
            self._mcp_server_limits[mcp_server_id] = limit
            # Replace the semaphore so future acquisitions use the new limit
            self._mcp_server_semaphores[mcp_server_id] = threading.Semaphore(limit) if limit > 0 else None

        logger.debug(
            "mcp_server_concurrency_limit_set",
            mcp_server_id=mcp_server_id,
            limit=limit if limit > 0 else "unlimited",
        )

    def get_mcp_server_limit(self, mcp_server_id: str) -> int:
        """Get the effective concurrency limit for a mcp_server.

        Args:
            mcp_server_id: McpServer identifier.

        Returns:
            Concurrency limit (0 = unlimited).
        """
        with self._lock:
            return self._mcp_server_limits.get(mcp_server_id, self._default_mcp_server_limit)

    def _get_mcp_server_semaphore(self, mcp_server_id: str) -> threading.Semaphore | None:
        """Get or create the semaphore for a mcp_server.

        Thread-safe. Creates lazily on first access.

        Args:
            mcp_server_id: McpServer identifier.

        Returns:
            Semaphore instance, or None if unlimited.
        """
        with self._lock:
            if mcp_server_id not in self._mcp_server_semaphores:
                limit = self._mcp_server_limits.get(mcp_server_id, self._default_mcp_server_limit)
                self._mcp_server_semaphores[mcp_server_id] = threading.Semaphore(limit) if limit > 0 else None
            return self._mcp_server_semaphores[mcp_server_id]

    @contextmanager
    def acquire(self, mcp_server_id: str) -> Generator[float, None, None]:
        """Acquire both global and mcp_server concurrency slots.

        This context manager acquires the global semaphore first, then the
        per-mcp_server semaphore (consistent ordering prevents deadlocks).
        It yields the time spent waiting for slots (in seconds).

        Metrics are updated on entry (inflight +1, wait time) and on
        exit (inflight -1).

        Args:
            mcp_server_id: McpServer identifier for per-mcp_server limiting.

        Yields:
            Wait time in seconds (time spent acquiring both semaphores).

        Example:
            with manager.acquire("math") as wait_s:
                if wait_s > 0.01:
                    logger.debug("waited for slot", wait_s=wait_s)
                result = invoke(...)
        """
        wait_start = time.monotonic()
        had_to_wait = False

        # --- Acquire global semaphore ---
        if self._global_semaphore is not None:
            acquired = self._global_semaphore.acquire(blocking=False)
            if not acquired:
                had_to_wait = True
                logger.debug(
                    "concurrency_global_wait_start",
                    mcp_server=mcp_server_id,
                    global_limit=self._global_limit,
                )
                self._global_semaphore.acquire(blocking=True)

        global_acquired = True

        try:
            # --- Acquire mcp_server semaphore ---
            mcp_server_sem = self._get_mcp_server_semaphore(mcp_server_id)
            if mcp_server_sem is not None:
                acquired = mcp_server_sem.acquire(blocking=False)
                if not acquired:
                    had_to_wait = True
                    mcp_server_limit = self.get_mcp_server_limit(mcp_server_id)
                    logger.debug(
                        "concurrency_mcp_server_wait_start",
                        mcp_server=mcp_server_id,
                        mcp_server_limit=mcp_server_limit,
                    )
                    mcp_server_sem.acquire(blocking=True)

            mcp_server_acquired = True

            try:
                # --- Record metrics ---
                wait_elapsed = time.monotonic() - wait_start
                BATCH_CONCURRENCY_WAIT_SECONDS.observe(wait_elapsed, mcp_server=mcp_server_id)

                if had_to_wait:
                    BATCH_CONCURRENCY_QUEUED_TOTAL.inc(mcp_server=mcp_server_id)
                    logger.debug(
                        "concurrency_slot_acquired_after_wait",
                        mcp_server=mcp_server_id,
                        wait_ms=round(wait_elapsed * 1000, 2),
                    )

                BATCH_INFLIGHT_CALLS.inc()
                BATCH_INFLIGHT_CALLS_PER_PROVIDER.inc(mcp_server=mcp_server_id)

                yield wait_elapsed

            finally:
                # --- Release mcp_server semaphore ---
                BATCH_INFLIGHT_CALLS.dec()
                BATCH_INFLIGHT_CALLS_PER_PROVIDER.dec(mcp_server=mcp_server_id)

                if mcp_server_sem is not None:
                    mcp_server_sem.release()
                mcp_server_acquired = False  # noqa: F841 (clarity)

        except BaseException:
            # If we failed to acquire the mcp_server semaphore (or anything
            # else went wrong before yield), release the global semaphore
            if global_acquired and self._global_semaphore is not None:
                self._global_semaphore.release()
            raise
        else:
            # Normal exit: release global semaphore
            if self._global_semaphore is not None:
                self._global_semaphore.release()

    def get_stats(self) -> dict[str, int | str | dict[str, int | str]]:
        """Get current concurrency statistics.

        Returns:
            Dictionary with global and per-mcp_server limits.
        """
        with self._lock:
            mcp_server_stats = {}
            for pid, limit in self._mcp_server_limits.items():
                mcp_server_stats[pid] = limit if limit > 0 else "unlimited"

            return {
                "global_limit": self._global_limit if self._global_limit > 0 else "unlimited",
                "default_mcp_server_limit": (
                    self._default_mcp_server_limit if self._default_mcp_server_limit > 0 else "unlimited"
                ),
                "mcp_server_overrides": mcp_server_stats,
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: ConcurrencyManager | None = None
_manager_lock = threading.Lock()


def get_concurrency_manager() -> ConcurrencyManager:
    """Get the global ConcurrencyManager singleton.

    Creates a default instance on first access. Use init_concurrency_manager()
    to configure before first use.

    Returns:
        ConcurrencyManager instance.
    """
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = ConcurrencyManager()
    return _manager


def init_concurrency_manager(
    global_limit: int = DEFAULT_GLOBAL_CONCURRENCY,
    default_mcp_server_limit: int = DEFAULT_PROVIDER_CONCURRENCY,
    mcp_server_limits: dict[str, int] | None = None,
) -> ConcurrencyManager:
    """Initialize the global ConcurrencyManager.

    Should be called during bootstrap, before any hangar_call invocations.

    Args:
        global_limit: Maximum total in-flight calls (0 = unlimited).
        default_mcp_server_limit: Default per-mcp_server limit (0 = unlimited).
        mcp_server_limits: Optional dict of mcp_server_id -> concurrency limit.

    Returns:
        Initialized ConcurrencyManager.
    """
    global _manager
    with _manager_lock:
        _manager = ConcurrencyManager(
            global_limit=global_limit,
            default_mcp_server_limit=default_mcp_server_limit,
        )
        if mcp_server_limits:
            for mcp_server_id, limit in mcp_server_limits.items():
                _manager.set_mcp_server_limit(mcp_server_id, limit)

    logger.info(
        "concurrency_manager_configured",
        global_limit=global_limit if global_limit > 0 else "unlimited",
        default_mcp_server_limit=(default_mcp_server_limit if default_mcp_server_limit > 0 else "unlimited"),
        mcp_server_overrides=len(mcp_server_limits) if mcp_server_limits else 0,
    )
    return _manager


def reset_concurrency_manager() -> None:
    """Reset the global ConcurrencyManager (for testing)."""
    global _manager
    with _manager_lock:
        _manager = None
