"""Concurrency management for batch execution.

Two levels of limit:
- Global: the calls in flight across all mcp_servers
- Per mcp_server: the calls in flight to each individual mcp_server

A call takes a slot at both levels before it executes, global first, then
mcp_server.

Each limit counts the calls holding its slots, and its size can change while
they run: a reload that changes a limit updates it in place, so a running call
keeps the slot it holds and its release frees that slot (#1432). The limits used
to be ``threading.Semaphore`` objects, which cannot be resized, and every reload
built new ones. The calls already running were then counted on the old ones
while new calls filled the new ones, so the effective limit could reach twice
the configured value.

This module uses threads (not asyncio) because the batch executor is
thread-based by design. The limits are shared across batches, providing
cross-batch backpressure that ThreadPoolExecutor alone cannot achieve.

Example:
    manager = ConcurrencyManager(global_limit=50, default_mcp_server_limit=10)
    manager.set_mcp_server_limit("slow-api", 3)

    with manager.acquire("fast-api") as wait_time:
        # At most 50 calls globally, at most 10 to fast-api
        result = mcp_server.invoke_tool(...)
"""

from collections.abc import Generator, Mapping
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


def _checked(name: str, limit: int) -> int:
    if limit < 0:
        raise ValueError(f"{name} must be >= 0, got {limit}")
    return limit


class _Limiter:
    """The calls holding and waiting for one limit's slots. Guarded by the manager's lock.

    The limit changes in place: a new limit applies to the calls that acquire
    after it, and a call already holding a slot keeps it until it releases.
    """

    __slots__ = ("active", "limit", "ready", "waiting")

    def __init__(self, limit: int, lock: threading.Lock) -> None:
        self.limit = limit
        self.active = 0
        self.waiting = 0
        self.ready = threading.Condition(lock)

    def full(self) -> bool:
        return self.limit > 0 and self.active >= self.limit

    def retune(self, limit: int) -> None:
        if limit != self.limit:
            self.limit = limit
            # A raised limit may let several waiters in; under a lowered one
            # they find it still full and wait again.
            self.ready.notify_all()


class ConcurrencyManager:
    """Two-level concurrency control: a global limit and one per mcp_server.

    A call must take a slot at both levels before executing, global first,
    then mcp_server.

    The manager is designed to be shared across multiple BatchExecutor
    invocations (i.e., across concurrent hangar_call batches), providing
    system-wide backpressure. A reload changes its limits in place and never
    replaces it; see the module docstring.

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
        self._global_limit = _checked("global_limit", global_limit)
        self._default_mcp_server_limit = _checked("default_mcp_server_limit", default_mcp_server_limit)

        # One lock guards every count and limit below; each limiter's
        # condition waits on it.
        self._lock = threading.Lock()
        self._global = _Limiter(global_limit, self._lock)
        #: The limiter of each mcp_server a call holds or waits for a slot on.
        #: Created by its first call and dropped when its last one releases,
        #: so a removed server's limiter goes once its calls have finished.
        self._limiters: dict[str, _Limiter] = {}
        #: The mcp_servers whose limit is not the default.
        self._mcp_server_limits: dict[str, int] = {}

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

    def set_limits(self, global_limit: int, default_mcp_server_limit: int) -> None:
        """Change the global and the default per-mcp_server limit, in place.

        What a configuration's ``execution`` section sets, on startup and on
        every reload. A call in flight keeps its slot, and a changed limit
        applies to the calls that acquire after it. An unchanged limit changes
        nothing.

        Raises:
            ValueError: If either limit is negative. Nothing is changed.
        """
        _checked("global_limit", global_limit)
        _checked("default_mcp_server_limit", default_mcp_server_limit)
        with self._lock:
            self._global_limit = global_limit
            self._default_mcp_server_limit = default_mcp_server_limit
            self._global.retune(global_limit)
            self._retune_mcp_servers()

    def set_mcp_server_limits(self, limits: Mapping[str, int], *, replace: bool = False) -> None:
        """Set the limit of each mcp_server in *limits*, in place.

        Args:
            limits: mcp_server id -> limit (0 = unlimited).
            replace: Whether *limits* are all the per-mcp_server limits there
                are, which is what a reload passes: an mcp_server left out goes
                back to the default. Without it the others are kept.

        Raises:
            ValueError: If a limit is negative. Nothing is changed.
        """
        for limit in limits.values():
            _checked("limit", limit)
        with self._lock:
            if replace:
                self._mcp_server_limits = dict(limits)
            else:
                self._mcp_server_limits.update(limits)
            self._retune_mcp_servers()

    def set_mcp_server_limit(self, mcp_server_id: str, limit: int) -> None:
        """Set concurrency limit for a specific mcp_server.

        In place: its in-flight calls keep their slots, and the new limit
        applies to the calls that acquire after it.

        Args:
            mcp_server_id: McpServer identifier.
            limit: Maximum concurrent calls for this mcp_server (0 = unlimited).

        Raises:
            ValueError: If limit is negative.
        """
        self.set_mcp_server_limits({mcp_server_id: limit})

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
            return self._limit_of(mcp_server_id)

    def in_flight(self, mcp_server_id: str | None = None) -> int:
        """The calls holding a slot: all of them, or those to one mcp_server."""
        with self._lock:
            if mcp_server_id is None:
                return self._global.active
            limiter = self._limiters.get(mcp_server_id)
            return limiter.active if limiter is not None else 0

    def _limit_of(self, mcp_server_id: str) -> int:
        """Called with the lock held."""
        return self._mcp_server_limits.get(mcp_server_id, self._default_mcp_server_limit)

    def _retune_mcp_servers(self) -> None:
        """Called with the lock held."""
        for mcp_server_id, limiter in self._limiters.items():
            limiter.retune(self._limit_of(mcp_server_id))

    @staticmethod
    def _take(limiter: _Limiter, waits: str, **fields: object) -> bool:
        """Take a slot on *limiter*, waiting for one while it is full. Called with the lock held.

        Args:
            waits: The event logged when the call has to wait.

        Returns:
            Whether it had to wait.
        """
        if not limiter.full():
            limiter.active += 1
            return False
        logger.debug(waits, limit=limiter.limit, **fields)
        limiter.waiting += 1
        try:
            while limiter.full():
                limiter.ready.wait()
        except BaseException:
            # A wake-up this waiter took belongs to the next one.
            limiter.ready.notify()
            raise
        finally:
            limiter.waiting -= 1
        limiter.active += 1
        return True

    @staticmethod
    def _give(limiter: _Limiter) -> None:
        """Release a slot on *limiter*. Called with the lock held."""
        limiter.active -= 1
        limiter.ready.notify()

    def _forget_if_idle(self, mcp_server_id: str, limiter: _Limiter) -> None:
        """Drop *limiter* once no call holds or waits for a slot on it. Called with the lock held."""
        if limiter.active == 0 and limiter.waiting == 0 and self._limiters.get(mcp_server_id) is limiter:
            del self._limiters[mcp_server_id]

    @contextmanager
    def acquire(self, mcp_server_id: str) -> Generator[float, None, None]:
        """Acquire both global and mcp_server concurrency slots.

        This context manager takes the global slot first, then the
        per-mcp_server one, and yields the time spent waiting for them (in
        seconds). The slots are released on the limiters they were taken on,
        whatever the limits have been changed to since.

        Metrics are updated on entry (inflight +1, wait time) and on
        exit (inflight -1).

        Args:
            mcp_server_id: McpServer identifier for per-mcp_server limiting.

        Yields:
            Wait time in seconds (time spent acquiring both slots).

        Example:
            with manager.acquire("math") as wait_s:
                if wait_s > 0.01:
                    logger.debug("waited for slot", wait_s=wait_s)
                result = invoke(...)
        """
        wait_start = time.monotonic()
        with self._lock:
            had_to_wait = self._take(self._global, "concurrency_global_wait_start", mcp_server=mcp_server_id)
            limiter = self._limiters.get(mcp_server_id)
            if limiter is None:
                limiter = self._limiters[mcp_server_id] = _Limiter(self._limit_of(mcp_server_id), self._lock)
            try:
                had_to_wait = (
                    self._take(limiter, "concurrency_mcp_server_wait_start", mcp_server=mcp_server_id) or had_to_wait
                )
            except BaseException:
                self._forget_if_idle(mcp_server_id, limiter)
                self._give(self._global)
                raise

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
            try:
                yield wait_elapsed
            finally:
                BATCH_INFLIGHT_CALLS.dec()
                BATCH_INFLIGHT_CALLS_PER_PROVIDER.dec(mcp_server=mcp_server_id)
        finally:
            with self._lock:
                self._give(limiter)
                self._forget_if_idle(mcp_server_id, limiter)
                self._give(self._global)

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
    """Replace the global ConcurrencyManager with a new one.

    A configuration does not use it: its limits are applied in place, with
    ``ConcurrencyManager.set_limits`` and ``set_mcp_server_limits``, because the
    calls running on a replaced manager would no longer be counted (#1432).

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
