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
from collections.abc import Coroutine
from typing import Any

from mcp_hangar.domain.contracts.fleet import IFleetWriter
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
    ) -> None:
        """Wrap an async config repository as a sync fleet writer.

        Args:
            config_repository: Whichever repository the storage backend chose.
            timeout_s: How long a single write may take before it is a failure.
        """
        self._repository = config_repository
        self._timeout_s = timeout_s
        self._loop = _Loop()

    def save(self, snapshot: McpServerConfigSnapshot) -> None:
        """Persist a server's configuration, waiting for the write."""
        self._loop.run(self._repository.save(snapshot), self._timeout_s)
        logger.debug("fleet_snapshot_saved", mcp_server_id=snapshot.mcp_server_id)

    def delete(self, mcp_server_id: str) -> None:
        """Remove a server's configuration, waiting for the write."""
        self._loop.run(self._repository.delete(mcp_server_id), self._timeout_s)
        logger.debug("fleet_snapshot_deleted", mcp_server_id=mcp_server_id)

    def close(self) -> None:
        """Stop the background loop. Called at shutdown; safe to call twice."""
        self._loop.close()
