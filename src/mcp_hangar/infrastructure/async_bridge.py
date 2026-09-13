"""Running an async repository from the sync side, and waiting for it.

The repositories are async: SQLite's genuinely so (aiosqlite), PostgreSQL's by
signature only. Several things that must use them are not -- the command bus is
sync end to end, and so is bootstrap. Something has to cross, and this is where.

Kept in one place because the crossing has a sharp edge that is not obvious and
is expensive to rediscover: see the note on the daemon thread below.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import threading
from typing import Any
import weakref

from mcp_hangar.application.ports.async_task import IBlockingAsyncRunner


class BackgroundLoop(IBlockingAsyncRunner):
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

    Daemon is not a licence to leak, though. An owner dropped without `close()`
    used to leave its thread running an empty loop for the life of the process
    -- 51 of them by the end of the unit suite (#1389). Now the loop stops when
    its owner is collected.
    """

    #: How long `close()` waits for the thread to leave its loop.
    JOIN_TIMEOUT_S = 5.0

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopper: Callable[[], object] | None = None

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
            # The thread holds the loop and never `self`, so an owner that is
            # dropped is still collected, and this stops its loop when it is.
            # Not at exit: the daemon thread needs nothing then.
            stopper = weakref.finalize(self, _stop, loop)
            stopper.atexit = False  # type: ignore[misc]  # settable at runtime; the stub says slots
            self._stopper = stopper
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
        """Stop the loop and wait for its thread. Safe to call twice."""
        if self._stopper is not None:
            self._stopper()  # a finalizer runs at most once
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(self.JOIN_TIMEOUT_S)
        self._loop = self._thread = self._stopper = None


def _stop(loop: asyncio.AbstractEventLoop) -> None:
    loop.call_soon_threadsafe(loop.stop)
