"""How ``serve --http`` stops with MCP requests in flight (#2097).

Two things went wrong on a stop. sse-starlette closes every open SSE stream as
soon as uvicorn starts shutting down, so the POST stream a pending ``tools/call``
answers on was cut and the client never got its response, while the call ran on
to completion upstream. And the batch a ``hangar_call`` runs blocks a worker
thread that cancelling the request task cannot stop, so the process lived as
long as its slowest upstream call whatever ``http.graceful_shutdown_timeout_s``
said.

:class:`DrainingServer` stops in this order instead:

1. It stops accepting connections, as uvicorn does.
2. It waits for every MCP POST in flight to be answered, and for every batch
   in flight to end -- without a bound when none is set, as before, and for at
   most the bound when one is.
3. When the bound runs out it abandons the batches in flight -- ``hangar_call``
   and the front door's tool calls: each returns at once and its caller is
   answered with a JSON-RPC error on the stream it is waiting on. It then gives
   those answers :data:`ABANDON_GRACE_S` to be written.
4. Anything still in flight after that is not a batch -- ``hangar_start``,
   ``hangar_warm``, a cold ``hangar_tools``, a resource read -- and has no
   answer to give. The server's components are stopped then, which ends the
   upstream work they wait on, and the process is ended shortly after if
   something still holds it. Those requests end with the connection.
5. Only then does it let sse-starlette close the streams still open -- the
   ``list_changed`` GET streams, which never end on their own -- and hands the
   rest of the stop to uvicorn, with what is left of the bound.

A 2026-07-28 ``subscriptions/listen`` POST is a stream that never ends on its
own, so it is not waited for.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from typing import Any

import uvicorn

from ..logging_config import get_logger
from .tools.batch.shutdown import abandon_in_flight_batches, in_flight_batch_count

logger = get_logger(__name__)

#: How long the answers to abandoned calls get to be written before the stop goes on.
ABANDON_GRACE_S = 2.0

#: The least uvicorn is given, after the drain, to close the streams still open.
STREAM_CLOSE_GRACE_S = 1.0

_POLL_S = 0.05

#: Methods whose POST answers with a stream that stays open: not a request to wait for.
_STREAM_METHODS = frozenset({"subscriptions/listen"})


def _is_stream(scope: Any) -> bool:
    # The 2026-07-28 era requires `Mcp-Method` and the SDK refuses a body that
    # disagrees with it, so the header is what the request is.
    method = next((value for key, value in scope.get("headers", ()) if key.lower() == b"mcp-method"), b"")
    return method.decode("latin-1") in _STREAM_METHODS


class McpRequestDrain:
    """ASGI wrapper around the MCP app that counts the POSTs it is still answering.

    A POST is counted until the app returns from it, which is when its response
    has been written -- or its client has gone.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self.in_flight = 0
        self.abandoned = 0
        #: Whether the stop went on with requests still in flight: the bound ran
        #: out, or uvicorn was told to quit at once.
        self.overran = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or _is_stream(scope):
            await self._app(scope, receive, send)
            return
        self.in_flight += 1
        try:
            await self._app(scope, receive, send)
        finally:
            self.in_flight -= 1

    def _busy(self) -> bool:
        return self.in_flight > 0 or in_flight_batch_count() > 0

    async def drain(self, bound_s: float | None, force_exit: Callable[[], bool]) -> None:
        """Wait until nothing is in flight; abandon what is left once *bound_s* has passed.

        Args:
            bound_s: The graceful-shutdown bound; None waits without one.
            force_exit: True once uvicorn was told to quit at once (a second
                Ctrl+C); the wait ends then.
        """
        loop = asyncio.get_running_loop()
        deadline = None if bound_s is None else loop.time() + bound_s
        if self._busy():
            logger.info(
                "http_shutdown_draining",
                requests_in_flight=self.in_flight,
                batches_in_flight=in_flight_batch_count(),
                graceful_shutdown_timeout_s=bound_s,
            )
        abandoned = False
        while self._busy():
            if force_exit():
                self.overran = True
                return
            now = loop.time()
            if deadline is not None and now >= deadline:
                if not abandoned:
                    abandoned = True
                    self.abandoned = abandon_in_flight_batches()
                    if self.abandoned:
                        logger.warning(
                            "http_shutdown_abandoned_calls",
                            batches=self.abandoned,
                            requests_in_flight=self.in_flight,
                            graceful_shutdown_timeout_s=bound_s,
                        )
                        deadline = now + ABANDON_GRACE_S
                        continue
                self.overran = True
                logger.warning(
                    "http_shutdown_bound_exceeded",
                    requests_in_flight=self.in_flight,
                    batches_in_flight=in_flight_batch_count(),
                    graceful_shutdown_timeout_s=bound_s,
                )
                return
            await asyncio.sleep(_POLL_S)


def _uvicorn_handle_exit() -> Callable[..., None]:
    """uvicorn's own ``Server.handle_exit``, from under sse-starlette's patch of it."""
    try:
        from sse_starlette.sse import AppStatus
    except ImportError:  # pragma: no cover -- the MCP SDK depends on sse-starlette
        return uvicorn.Server.handle_exit
    original = getattr(AppStatus, "original_handler", None)
    return original if callable(original) else uvicorn.Server.handle_exit


class DrainingServer(uvicorn.Server):
    """A ``uvicorn.Server`` whose stop drains the MCP requests in flight first.

    Args:
        config: The uvicorn configuration.
        drain: The wrapper counting the MCP requests in flight.
        on_overrun: Run on a worker thread when the bound ran out with requests
            still in flight, before the rest of the stop: what ends the work
            they wait on.
    """

    def __init__(
        self, config: uvicorn.Config, drain: McpRequestDrain, on_overrun: Callable[[], None] | None = None
    ) -> None:
        super().__init__(config)
        self._drain = drain
        self._on_overrun = on_overrun

    def handle_exit(self, sig: int, frame: Any) -> None:
        # Not through sse-starlette's patch, which ends every SSE stream the
        # moment the signal lands; `shutdown` lets it once the drain is over.
        _uvicorn_handle_exit()(self, sig, frame)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        # Stop accepting first, as uvicorn's own shutdown does: no new
        # connection, and no new request on a kept-alive one.
        for server in self.servers:
            server.close()
        for sock in sockets or []:
            sock.close()
        for connection in list(self.server_state.connections):
            connection.shutdown()

        bound = self.config.timeout_graceful_shutdown
        try:
            await self._drain.drain(bound, lambda: self.force_exit)
            if self._drain.overran and not self.force_exit and self._on_overrun is not None:
                await asyncio.to_thread(self._on_overrun)
        finally:
            # Nothing else ends the GET streams now: sse-starlette was told not to.
            _end_sse_streams()

        if bound is None:
            await super().shutdown(sockets)
            return
        # The drain spent part of the bound; uvicorn gets what is left of it.
        self.config.timeout_graceful_shutdown = max(bound - (loop.time() - started), STREAM_CLOSE_GRACE_S)  # type: ignore[assignment]
        try:
            await super().shutdown(sockets)
        finally:
            self.config.timeout_graceful_shutdown = bound


def _end_sse_streams() -> None:
    try:
        from sse_starlette.sse import AppStatus
    except ImportError:  # pragma: no cover
        return
    AppStatus.should_exit = True


class SseDrainHeld:
    """Keeps sse-starlette from ending streams on its own while ``serve --http`` runs.

    sse-starlette also watches uvicorn's ``should_exit`` directly unless its
    automatic drain is off. The previous settings come back on exit, so a test
    that serves in-process leaves the library as it found it.
    """

    def __enter__(self) -> SseDrainHeld:
        try:
            from sse_starlette.sse import AppStatus
        except ImportError:  # pragma: no cover
            self._status = None
            return self
        self._status = AppStatus
        self._saved = (AppStatus.should_exit, getattr(AppStatus, "enable_automatic_graceful_drain", True))
        AppStatus.enable_automatic_graceful_drain = False
        return self

    def __exit__(self, *exc: object) -> None:
        if self._status is not None:
            self._status.should_exit, self._status.enable_automatic_graceful_drain = self._saved


__all__ = ["ABANDON_GRACE_S", "DrainingServer", "McpRequestDrain", "SseDrainHeld"]
