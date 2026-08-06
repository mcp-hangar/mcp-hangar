"""The sync fleet writer, over whatever the storage backend's repository is.

The repositories are async: SQLite's genuinely so (aiosqlite), PostgreSQL's by
signature only. The command path that has to write them is sync -- the API
dispatches commands into a threadpool, discovery calls `send` directly -- so
something has to cross, and the choice is where.

It crosses here rather than in the handler. The application layer asks a port to
save a snapshot; that the answer involves a coroutine on another thread's event
loop is an adapter's problem, and putting it in the handler would put
`asyncio`-shaped code in the one layer that should be free of it.

**It waits, and it raises.** `AsyncExecutor.submit` is fire-and-forget, which
would let a registration answer "created" while its durable half failed -- the
exact failure this writer exists to remove. Waiting costs the command path the
latency of one row write, on registration only.
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from mcp_hangar.domain.contracts.fleet import IFleetWriter, NotTheManagerError
from mcp_hangar.domain.contracts.management_lease import Lease
from mcp_hangar.domain.contracts.persistence import IMcpServerConfigRepository, McpServerConfigSnapshot
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


class _Loop:
    """One background thread with one event loop, reused across calls.

    A fresh `asyncio.run` per write would be simpler, and it is what the
    fire-and-forget executor does. It is wrong here: aiosqlite starts a thread
    per connection, and tearing the loop down after every registration closes
    connections the repository still expects to reuse.

    A single long-lived loop also means the calling thread can block on a future
    without deadlocking, since the work never runs on the caller's own loop.

    **The thread is a daemon, and that is not a detail.** A `ThreadPoolExecutor`
    was the obvious way to get one and it hangs the process on exit: its threads
    are non-daemon, CPython joins non-daemon threads *before* it runs `atexit`
    handlers, and the handler that would have stopped this loop never gets to
    run. The result is a gateway that finishes its work and never exits. A
    daemon thread has nothing waiting on it; `close()` stops the loop for the
    orderly case, and interpreter exit does not need it to.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run,
                args=(loop,),
                name="fleet-writer",
                daemon=True,
            )
            self._thread.start()
            self._loop = loop
        return self._loop

    @staticmethod
    def _run(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        """Run `coro` on the background loop and wait for it."""
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure())
        return future.result(timeout=timeout)

    def close(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None
        self._thread = None


class RepositoryFleetWriter(IFleetWriter):
    """Writes the fleet through the selected backend's config repository."""

    #: A row write. Generous enough that a slow disk or a busy pool does not
    #: turn into a failed registration, short enough that a wedged database
    #: fails the command rather than hanging the request forever.
    DEFAULT_TIMEOUT_S = 15.0

    def __init__(
        self,
        config_repository: IMcpServerConfigRepository,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        lease_provider: Callable[[], Lease | None] | None = None,
    ) -> None:
        """Wrap an async config repository as a sync fleet writer.

        Args:
            config_repository: Whichever repository the storage backend chose.
            timeout_s: How long a single write may take before it is a failure.
            lease_provider: The tenure this instance believes it holds, asked
                per write. **Absent and returning None are different answers**:
                absent means nothing is coordinating -- a standalone gateway,
                which is its own manager and needs no fence. Returning None
                means this instance *is* coordinating and currently holds
                nothing, so a convergence loop's write must be refused rather
                than let through unfenced. Conflating the two is how the window
                between losing the lease and noticing gets left open.
        """
        self._repository = config_repository
        self._timeout_s = timeout_s
        self._lease_provider = lease_provider
        self._loop = _Loop()

    def save(self, snapshot: McpServerConfigSnapshot) -> None:
        """Persist a server's configuration, waiting for the write."""
        self._loop.run(self._repository.save(snapshot), self._timeout_s)
        logger.debug("fleet_snapshot_saved", mcp_server_id=snapshot.mcp_server_id)

    def delete(self, mcp_server_id: str, *, fenced: bool = False) -> None:
        """Remove a server's configuration, waiting for the write.

        See `IFleetWriter.delete` for what `fenced` means and the sequence it
        exists for. The tenure comes from this instance's own belief, and the
        database decides whether that belief is still true -- reading it here is
        not the check, it only supplies the claim.
        """
        if not fenced or self._lease_provider is None:
            self._loop.run(self._repository.delete(mcp_server_id), self._timeout_s)
            logger.debug("fleet_snapshot_deleted", mcp_server_id=mcp_server_id)
            return

        lease = self._lease_provider()
        if lease is None:
            raise NotTheManagerError(mcp_server_id)

        fenced_delete = getattr(self._repository, "delete_while_leased", None)
        if fenced_delete is None:
            # A repository that cannot express the condition must not perform
            # the deletion unconditionally instead. Silently dropping the fence
            # is worse than refusing: the deletion looks like it worked.
            raise NotImplementedError(
                f"{type(self._repository).__name__} cannot fence a deregistration by lease generation; "
                "a convergence loop's deletion will not be performed unfenced"
            )

        deleted = self._loop.run(fenced_delete(mcp_server_id, lease.holder, lease.generation), self._timeout_s)
        if deleted:
            logger.debug("fleet_snapshot_deleted", mcp_server_id=mcp_server_id, generation=lease.generation)
            return

        # Zero rows. Either the tenure ended between the decision and the write
        # -- the case this exists for -- or the row was already gone. Both are
        # fine outcomes for a convergence loop, and worth a line either way,
        # because "the delete did nothing" is otherwise invisible.
        logger.info(
            "fleet_deregistration_not_applied",
            mcp_server_id=mcp_server_id,
            generation=lease.generation,
            detail="the row was already absent, or this tenure had ended by the time the write reached the database",
        )

    def close(self) -> None:
        """Stop the background loop. Called at shutdown; safe to call twice."""
        self._loop.close()
