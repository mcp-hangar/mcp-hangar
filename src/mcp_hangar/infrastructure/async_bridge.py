"""Running an async repository from the sync side, and waiting for it.

The repositories are async: SQLite's genuinely so (aiosqlite), PostgreSQL's by
signature only. Several things that must use them are not -- the command bus is
sync end to end, and so is bootstrap. Something has to cross, and this is where.

Kept in one place because the crossing has a sharp edge that is not obvious and
is expensive to rediscover: see the note on the daemon thread below.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import threading
from typing import Any


class BackgroundLoop:
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
