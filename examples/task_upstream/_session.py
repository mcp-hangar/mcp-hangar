"""A tiny raw JSON-RPC session over streamable HTTP, shared by the driver scripts.

The drivers speak raw JSON-RPC on purpose. ``tasks/*`` are custom methods on
``mcp==2.0.0b2`` (no typed client helpers yet), and the point of these scripts is
to assert the exact wire shapes Hangar's relay depends on -- a typed client would
hide them. This module is only the plumbing: request/response correlation and
answering server-initiated requests (Hangar's consent elicitation).

Not a general-purpose client: no reconnection, no cancellation, single page of
results, ``anyio`` timeouts only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

#: Protocol revision the drivers negotiate. Handshake-era on purpose: Hangar's
#: relay and the ``hangar_call`` meta-tool are driven over a session here, and
#: the upstream advertises the Tasks extension at ``initialize``.
PROTOCOL_VERSION = "2025-11-25"

#: Answers a server-initiated request. Receives (method, params) and returns the
#: JSON-RPC ``result`` to send back.
InboundHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class JsonRpcError(RuntimeError):
    """A JSON-RPC error response, kept structured so callers can assert on it."""

    def __init__(self, method: str, error: Any) -> None:
        self.method = method
        self.code = getattr(error, "code", None)
        self.message = getattr(error, "message", str(error))
        super().__init__(f"{method} -> [{self.code}] {self.message}")


class Session:
    """An initialized JSON-RPC session against one MCP endpoint."""

    def __init__(self, write: Any, on_inbound_request: InboundHandler | None) -> None:
        self._write = write
        self._on_inbound_request = on_inbound_request
        self._next_id = 0
        self._waiters: dict[int, anyio.Event] = {}
        self._responses: dict[int, Any] = {}
        #: Methods of server-initiated requests received, in order. Lets a driver
        #: assert that Hangar actually prompted (e.g. ``elicitation/create``).
        self.inbound_methods: list[str] = []
        self.initialize_result: dict[str, Any] = {}

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        """Send a request and return its ``result``; raise :class:`JsonRpcError`."""
        self._next_id += 1
        request_id = self._next_id
        event = anyio.Event()
        self._waiters[request_id] = event
        await self._write.send(
            SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params or {}))
        )
        with anyio.fail_after(timeout):
            await event.wait()
        response = self._responses.pop(request_id)
        self._waiters.pop(request_id, None)
        error = getattr(response, "error", None)
        if error is not None:
            raise JsonRpcError(method, error)
        return response.result

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write.send(SessionMessage(JSONRPCNotification(jsonrpc="2.0", method=method, params=params or {})))

    async def _dispatch(self, root: Any) -> None:
        """Route one inbound message: response, server request, or notification."""
        method = getattr(root, "method", None)
        message_id = getattr(root, "id", None)

        if method is not None and message_id is not None:
            # Server-initiated request (e.g. Hangar's elicitation/create).
            self.inbound_methods.append(method)
            result: dict[str, Any] = {}
            if self._on_inbound_request is not None:
                result = await self._on_inbound_request(method, getattr(root, "params", None) or {})
            await self._write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=message_id, result=result)))
            return

        if method is not None:  # notification -- nothing here needs them
            return

        waiter = self._waiters.get(message_id)
        if waiter is not None:
            self._responses[message_id] = root
            waiter.set()


@asynccontextmanager
async def open_session(
    url: str,
    *,
    client_name: str = "task-upstream-example",
    capabilities: dict[str, Any] | None = None,
    on_inbound_request: InboundHandler | None = None,
):
    """Open, initialize and yield a :class:`Session` against *url*.

    Declare ``capabilities={"elicitation": {}}`` to receive Hangar's mid-flight
    consent prompt; without it the gate fails closed instead of asking.
    """
    async with streamable_http_client(url) as (read, write, *_rest):
        session = Session(write, on_inbound_request)
        async with anyio.create_task_group() as task_group:

            async def pump() -> None:
                async for message in read:
                    if isinstance(message, Exception):
                        continue
                    await session._dispatch(message.message)

            task_group.start_soon(pump)
            session.initialize_result = await session.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": capabilities or {},
                    "clientInfo": {"name": client_name, "version": "0"},
                },
            )
            await session.notify("notifications/initialized")
            try:
                yield session
            finally:
                task_group.cancel_scope.cancel()


class Checks:
    """Collects pass/fail lines so a driver can print a summary and set exit code."""

    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        (self.passed if condition else self.failed).append(name)
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""), flush=True)
        return condition

    def summary(self) -> int:
        """Print the tally and return a process exit code."""
        total = len(self.passed) + len(self.failed)
        if self.failed:
            print(f"\n{len(self.passed)}/{total} passed -- FAILED: {', '.join(self.failed)}")
            return 1
        print(f"\n{len(self.passed)}/{total} passed -- ALL GREEN")
        return 0


def task_id_from_hangar_call(batch_result: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Dig the relayed task id out of a ``hangar_call`` batch result.

    In egress topology a client does not call the upstream tool directly: it calls
    Hangar's ``hangar_call`` meta-tool, so the upstream's ``{"task": {...}}``
    handle arrives nested inside the batch envelope. Returns
    ``(task_id, first_result)`` -- ``task_id`` is ``None`` when the relay refused
    (e.g. ``TaskRelayNotSupported``), and the caller prints ``first_result`` to
    show why. Note ``taskId`` is camelCase on the wire.
    """
    structured = batch_result.get("structuredContent") or {}
    first = (structured.get("results") or [{}])[0]
    task = ((first.get("result") or {}).get("task")) or {}
    return task.get("taskId"), first
