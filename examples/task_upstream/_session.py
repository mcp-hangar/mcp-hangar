"""Client plumbing shared by the task-relay drivers.

Two clients live here, one per protocol generation, because this example sits on
both sides of the split Hangar bridges.

## The modern path (``open_client``) -- drives Hangar

Built on the SDK's :class:`Client`, and every part of that is forced rather than
stylistic.

*Typed requests*, because SEP-2663 requires every ``tasks/*`` request to carry
``Mcp-Name: <taskId>`` so an intermediary can route a poll without parsing the
body, and Hangar enforces it. That header varies **per request**, while
``streamable_http_client`` only takes a connection-level ``http_client`` -- so a
hand-rolled client writing JSON-RPC onto the stream cannot comply at all. The
previous version of this file did exactly that, and is why these drivers stopped
working when the relay moved to the SEP-2663 wire.
``ClientSession.send_request`` stamps the header for any request type declaring
``name_param``; python-sdk#3005 gives the ecosystem the same shape, and when it
merges these local definitions are replaced by the SDK's.

*An explicit* ``mode="2026-07-28"``, because ``HANDSHAKE_PROTOCOL_VERSIONS`` tops
out at ``2025-11-25``: the ``initialize`` handshake **cannot** negotiate
2026-07-28. That generation is reached only on the per-request-envelope path,
where the version and the client capabilities ride in ``params._meta``. A plain
``ClientSession`` lands on a legacy connection, where SEP-2663 ``tasks/*``
correctly do not exist -- so it could never reach this surface no matter what it
declared.

*Raw dicts for results*, because validating them against
``mcp_hangar.tasks_wire`` would make the smoke test circular: it would prove our
models parse our own output. Asserting on literal wire keys (``taskId``,
``ttlMs``, ``resultType``) is the only version that can catch us serving the
wrong shape.

## The legacy path (``open_session``) -- drives the example upstream

Raw JSON-RPC on 2025-11-25. The example upstream is deliberately on the older
design, so ``smoke_upstream.py`` needs a client that speaks it. Nothing there can
reach Hangar's SEP-2663 surface, and it is not meant to.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

import anyio
from mcp.client.client import Client, ClientExtension
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp_types import Implementation, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse, Request, RequestParams
from pydantic import TypeAdapter

#: The extension Hangar gates `tasks/*` on. A client that does not declare it is
#: answered `-32021` with a machine-readable `requiredCapabilities` payload.
TASKS_EXTENSION = "io.modelcontextprotocol/tasks"

#: The generation SEP-2663 belongs to. Passed as ``Client(mode=...)`` because the
#: ``initialize`` handshake cannot negotiate it -- see the module docstring.
MODERN_VERSION = "2026-07-28"

#: What the legacy raw-JSON-RPC session negotiates; the example upstream is here.
PROTOCOL_VERSION = "2025-11-25"

#: Permissive: the point is to inspect what the server actually put on the wire.
RAW: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


class TaskIdParams(RequestParams):
    """``taskId`` on the wire (``RequestParams`` supplies the camelCase alias)."""

    task_id: str


class UpdateTaskParams(RequestParams):
    """``tasks/update``: answers keyed by the snapshot's ``inputRequests``."""

    task_id: str
    input_responses: dict[str, Any]


class GetTaskRequest(Request[TaskIdParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    params: TaskIdParams
    # The WIRE key, not the Python name: the stamp reads it off a by-alias dump.
    name_param = "taskId"


class CancelTaskRequest(Request[TaskIdParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: TaskIdParams
    name_param = "taskId"


class UpdateTaskRequest(Request[UpdateTaskParams, Literal["tasks/update"]]):
    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskParams
    name_param = "taskId"


class DiscoverRequest(Request[RequestParams, Literal["server/discover"]]):
    """SEP-2575 ``server/discover``: what a stateless client learns instead of ``initialize``.

    This is where a 2026-07-28 client reads capabilities from. ``Client`` exposes
    a ``server_capabilities`` attribute, but on this path it stays unset unless
    something triggers discovery -- asserting on it reports "no tasks capability"
    for a server that advertises one perfectly well, which is exactly the false
    alarm this request exists to avoid.
    """

    method: Literal["server/discover"] = "server/discover"
    params: RequestParams = RequestParams()


class RemovedMethodRequest(Request[TaskIdParams, str]):
    """A ``tasks/*`` method SEP-2663 removed, so a driver can prove it is gone.

    Carries ``name_param`` too: the rejection must be earned by the method being
    absent, not by the request tripping the routing gate on its way in.
    """

    method: str
    params: TaskIdParams
    name_param = "taskId"


class TasksExtension(ClientExtension):
    """Declares ``io.modelcontextprotocol/tasks`` on the request envelope."""

    identifier = TASKS_EXTENSION


@asynccontextmanager
async def open_client(url: str, *, client_name: str = "task-upstream-example", declare_tasks: bool = True):
    """Open a 2026-07-28 connection to *url* and yield the :class:`Client`.

    ``declare_tasks=False`` omits the extension declaration, which is how a
    driver exercises the ``-32021`` rung.
    """
    async with Client(
        url,
        mode=MODERN_VERSION,
        client_info=Implementation(name=client_name, version="0"),
        extensions=[TasksExtension()] if declare_tasks else None,
        raise_exceptions=True,
    ) as client:
        yield client


async def discover(session: ClientSession) -> dict[str, Any]:
    return await session.send_request(DiscoverRequest(), RAW)


async def get_task(session: ClientSession, task_id: str) -> dict[str, Any]:
    return await session.send_request(GetTaskRequest(params=TaskIdParams(task_id=task_id)), RAW)


async def cancel_task(session: ClientSession, task_id: str) -> dict[str, Any]:
    return await session.send_request(CancelTaskRequest(params=TaskIdParams(task_id=task_id)), RAW)


async def update_task(session: ClientSession, task_id: str, answers: dict[str, Any]) -> dict[str, Any]:
    return await session.send_request(
        UpdateTaskRequest(params=UpdateTaskParams(task_id=task_id, input_responses=answers)), RAW
    )


async def call_removed(session: ClientSession, method: str, task_id: str) -> dict[str, Any]:
    return await session.send_request(RemovedMethodRequest(method=method, params=TaskIdParams(task_id=task_id)), RAW)


# ---------------------------------------------------------------------------
# Legacy path: raw JSON-RPC on 2025-11-25, for the example upstream.
# ---------------------------------------------------------------------------

#: Answers a server-initiated request: (method, params) -> the JSON-RPC result.
InboundHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class JsonRpcError(RuntimeError):
    """A JSON-RPC error response, kept structured so callers can assert on it."""

    def __init__(self, method: str, error: Any) -> None:
        self.method = method
        self.code = getattr(error, "code", None)
        self.message = getattr(error, "message", str(error))
        super().__init__(f"{method} -> [{self.code}] {self.message}")


class Session:
    """An initialized raw JSON-RPC session against one MCP endpoint."""

    def __init__(self, write: Any, on_inbound_request: InboundHandler | None) -> None:
        self._write = write
        self._on_inbound_request = on_inbound_request
        self._next_id = 0
        self._waiters: dict[int, anyio.Event] = {}
        self._responses: dict[int, Any] = {}
        self.inbound_methods: list[str] = []
        self.initialize_result: dict[str, Any] = {}

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
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
    """Open, initialize and yield a raw :class:`Session` against *url*."""
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


def task_id_from_hangar_call(batch_result: Any) -> tuple[str | None, dict[str, Any]]:
    """Dig the relayed task id out of a ``hangar_call`` batch result.

    In egress topology a client does not call the upstream tool directly: it
    calls Hangar's ``hangar_call`` meta-tool, so the task handle arrives nested
    in the batch envelope. ``task_id`` is ``None`` when the relay refused (e.g.
    the kill-switch is off), and the caller prints ``first_result`` to show why.

    Accepts both handle shapes on purpose: this upstream still emits the nested
    ``{"task": {...}}``, while SEP-2663 puts ``taskId`` flat -- and which appears
    depends on the upstream, not on Hangar, so the driver must not care.
    """
    structured = getattr(batch_result, "structured_content", None) or {}
    if not structured and isinstance(batch_result, dict):
        structured = batch_result.get("structuredContent") or {}
    first = (structured.get("results") or [{}])[0]
    result = first.get("result") or {}
    task_id = result.get("taskId") or (result.get("task") or {}).get("taskId")
    return task_id, first
