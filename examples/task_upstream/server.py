"""Task-emitting upstream MCP server on the v2-native Tasks extension (mcp==2.0.0b2).

An *upstream* server meant to sit behind mcp-hangar's governed task relay
(ADR-014). A tool call does not return inline content; it returns a task handle
(``CreateTaskResult`` -> wire ``{"task": {...}}``) and the server serves the
native ``tasks/*`` lifecycle so a client can follow the task to completion.

Tools
-----
``long_job``          working -> completed (payload ready via ``tasks/result``)
``long_job_consent``  working -> input_required (awaits ``tasks/update``); this is
                      the branch that exercises Hangar's mid-flight consent gate
``echo``              a plain, non-task tool, so one server covers both shapes

Why the machinery below is hand-rolled
--------------------------------------
``mcp==2.0.0b2`` ships the Tasks *types* (``CreateTaskResult``, ``Task``,
``GetTaskResult`` ...) and the negotiated-extension plumbing, but it does NOT
ship a server-side task store or a ``tools/call`` -> task path: the high-level
``MCPServer._handle_call_tool`` only ever returns ``CallToolResult`` /
``InputRequiredResult``, and the runner serializes ``tools/call`` (a
``SPEC_CLIENT_METHODS`` method) strictly as ``CallToolResult`` -- a raw
``{"task": {...}}`` body fails that validation. So two things are hand-rolled:

1.  A server middleware short-circuits ``tools/call`` for the task tools and
    returns a ``CreateTaskResult`` *before* the runner's spec-serialize sieve
    runs. A middleware that returns without calling ``call_next`` is trusted to
    return its own wire result (the runner dumps a BaseModel by alias), which
    cleanly bypasses the ``CallToolResult`` validation.
2.  The ``tasks/*`` methods are registered on the low-level server via
    ``add_request_handler``. They are deliberately NOT in ``SPEC_CLIENT_METHODS``
    on b2, so the runner returns their result shape raw -- exactly the property
    mcp-hangar's relay seam relies on.

Expect this file to shrink as the SDK lands the server-side Tasks surface; the
pin is exact (``mcp==2.0.0b2``) for that reason. This mirrors, from the upstream
side, the contract Hangar drives from the relay side in
``fastmcp_server/task_relay_handlers.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from typing import Any
import uuid

import mcp_types as t
from mcp.server import MCPServer
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from pydantic import AliasChoices, Field

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

# Bind on 0.0.0.0:8080 by default (container / k8s mode). Overridable via env for
# local runs without touching the container defaults.
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8080"))
STREAMABLE_HTTP_PATH = os.environ.get("MCP_HTTP_PATH", "/mcp")

# Wall-clock a task spends "working" before it resolves. Short so a smoke test
# finishes quickly; raise it to watch polling or to test timeouts.
WORK_SECONDS = float(os.environ.get("MCP_TASK_WORK_SECONDS", "2.0"))
TASK_TTL_MS = int(os.environ.get("MCP_TASK_TTL_MS", "60000"))

# The task-emitting tools. A tools/call for either name is intercepted by the
# middleware and turned into a task handle instead of an inline result.
TASK_TOOL = "long_job"
CONSENT_TOOL = "long_job_consent"
_TASK_TOOLS = frozenset({TASK_TOOL, CONSENT_TOOL})


def _now() -> str:
    """ISO-8601 UTC timestamp (``...Z``)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# In-memory task store (the b2 SDK ships no server-side task store)
# --------------------------------------------------------------------------- #


class _TaskRecord:
    """One task's mutable state plus the payload it will complete with."""

    __slots__ = (
        "task_id",
        "tool",
        "prompt",
        "status",
        "status_message",
        "created_at",
        "last_updated_at",
        "result_text",
    )

    def __init__(self, task_id: str, tool: str, prompt: str) -> None:
        self.task_id = task_id
        self.tool = tool
        self.prompt = prompt
        self.status: t.TaskStatus = "working"
        self.status_message: str | None = None
        self.created_at = _now()
        self.last_updated_at = self.created_at
        self.result_text: str | None = None

    def touch(self) -> None:
        self.last_updated_at = _now()

    def _common(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "status_message": self.status_message,
            "created_at": self.created_at,
            "last_updated_at": self.last_updated_at,
            "ttl": TASK_TTL_MS,
        }

    def to_task(self) -> t.Task:
        """Project to a wire ``Task`` (used by tasks/list and the create handle)."""
        return t.Task(**self._common())

    def to_get_result(self) -> t.GetTaskResult:
        return t.GetTaskResult(**self._common())

    def to_cancel_result(self) -> t.CancelTaskResult:
        return t.CancelTaskResult(**self._common())


class TaskStore:
    """Trivial in-process task store with async background state transitions."""

    def __init__(self) -> None:
        self._tasks: dict[str, _TaskRecord] = {}

    def create(self, tool: str, prompt: str) -> _TaskRecord:
        record = _TaskRecord(uuid.uuid4().hex, tool, prompt)
        self._tasks[record.task_id] = record
        # Drive the lifecycle on the running loop -- we are inside an async
        # request handler, so a loop is guaranteed to be running.
        asyncio.create_task(self._run_lifecycle(record.task_id))
        return record

    def get(self, task_id: str) -> _TaskRecord:
        record = self._tasks.get(task_id)
        if record is None:
            raise MCPError(t.INVALID_PARAMS, f"Task not found: {task_id}")
        return record

    def list(self) -> list[_TaskRecord]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> _TaskRecord:
        record = self.get(task_id)
        if record.status not in ("completed", "failed"):
            record.status = "cancelled"
            record.status_message = "Cancelled by client"
            record.touch()
        return record

    def resolve_input(self, task_id: str) -> _TaskRecord:
        """Client supplied the requested input (``tasks/update``): resume to done."""
        record = self.get(task_id)
        if record.status == "input_required":
            record.status = "completed"
            record.status_message = None
            record.result_text = f"Completed (after consent) job for prompt: {record.prompt}"
            record.touch()
        return record

    async def _run_lifecycle(self, task_id: str) -> None:
        """Background: after a short delay, resolve the task.

        ``long_job`` completes with a payload; ``long_job_consent`` parks in
        ``input_required`` awaiting ``tasks/update``. A task cancelled meanwhile
        is left cancelled.
        """
        await asyncio.sleep(WORK_SECONDS)
        record = self._tasks.get(task_id)
        if record is None or record.status != "working":
            return
        if record.tool == CONSENT_TOOL:
            record.status = "input_required"
            record.status_message = "Additional input is required to continue. Do you consent?"
        else:
            record.status = "completed"
            record.result_text = f"Completed job for prompt: {record.prompt}"
        record.touch()

    def result_payload(self, task_id: str) -> t.CallToolResult:
        """The tool result for a *completed* task (wire ``tasks/result``)."""
        record = self.get(task_id)
        if record.status != "completed":
            raise MCPError(t.INVALID_PARAMS, f"Task {task_id} is not completed (status={record.status})")
        return t.CallToolResult(
            content=[t.TextContent(type="text", text=record.result_text or "")],
            is_error=False,
        )


STORE = TaskStore()


# --------------------------------------------------------------------------- #
# Server + task-emitting middleware
# --------------------------------------------------------------------------- #

mcp: MCPServer = MCPServer(name="task-upstream", version="0.1.0")


@mcp.tool(
    name=TASK_TOOL,
    description="Start a long-running job. Returns a task handle; follow it with tasks/*.",
    structured_output=False,
)
def long_job(prompt: str) -> str:
    """Schema-only stub.

    The body never executes: the task middleware intercepts ``tools/call`` for
    this tool and returns a task handle. Declared with a stable typed signature
    so the advertised ``inputSchema`` -- and therefore Hangar's pinned tool
    digest -- is fixed for the task's lifetime.
    """
    return "unreachable: long_job runs as a task"  # pragma: no cover


@mcp.tool(
    name=CONSENT_TOOL,
    description="Start a job that pauses for consent (parks in input_required).",
    structured_output=False,
)
def long_job_consent(prompt: str) -> str:
    """Schema-only stub; see :func:`long_job`. Parks the task in input_required."""
    return "unreachable: long_job_consent runs as a task"  # pragma: no cover


@mcp.tool(name="echo", description="Return the input unchanged (a plain, non-task tool).")
def echo(text: str) -> str:
    """A normal inline tool, so this one server covers both result shapes.

    Useful when checking that adding the Tasks surface did not disturb ordinary
    synchronous calls through Hangar.
    """
    return text


async def task_emitting_middleware(ctx: ServerRequestContext[Any, Any], call_next: Any) -> Any:
    """Turn ``tools/call`` for a task tool into a ``CreateTaskResult`` handle.

    Runs at the top of the request pipeline, before the runner's spec-method
    serialize sieve. For the task tools it short-circuits WITHOUT calling
    ``call_next`` and returns a ``CreateTaskResult``, which the runner dumps by
    alias to the wire ``{"task": {...}}`` -- bypassing the ``CallToolResult``-only
    validation the spec path would impose. Everything else (initialize,
    tools/list, tasks/*, notifications, the ``echo`` tool) passes straight
    through to the real handler.
    """
    if ctx.method == "tools/call" and isinstance(ctx.params, dict):
        name = ctx.params.get("name")
        if name in _TASK_TOOLS:
            arguments = ctx.params.get("arguments") or {}
            record = STORE.create(name, str(arguments.get("prompt", "")))
            return t.CreateTaskResult(task=record.to_task())
    return await call_next(ctx)


# --------------------------------------------------------------------------- #
# Native tasks/* request handlers (custom methods -> returned raw by the runner)
# --------------------------------------------------------------------------- #

# The low-level runner validates a custom method's params by ALIAS only
# (``model_validate(..., by_name=False)``). The SDK's own ``GetTaskRequestParams``
# et al. alias ``task_id`` -> ``taskId``, so they accept ONLY camelCase. A native
# v2 SDK client sends camelCase, while mcp-hangar's relay forwards snake_case
# (``{"task_id": ...}``), so validate against local models whose ``AliasChoices``
# accept BOTH spellings -- robust to either caller. ``extra="ignore"`` lets the
# relay's extra keys (e.g. ``input_key`` on tasks/update) pass through harmlessly.


class _TaskIdParams(t.RequestParams):
    """``task_id`` accepting snake_case (Hangar relay) or camelCase (SDK client)."""

    model_config = {"extra": "ignore", "populate_by_name": True}
    task_id: str = Field(validation_alias=AliasChoices("task_id", "taskId"))


class _ListParams(t.RequestParams):
    """Paginated params (cursor unused -- the store returns a single page)."""

    model_config = {"extra": "ignore", "populate_by_name": True}
    cursor: str | None = Field(default=None)


#: What a paused task says it needs, keyed the way SEP-2663 keys it. A client
#: answers these keys back through ``tasks/update``.
INPUT_REQUESTS = {
    "consent": {
        "message": "Additional input is required to continue. Do you consent?",
        "requestedSchema": {"type": "object", "properties": {}},
    }
}


async def handle_tasks_get(ctx: ServerRequestContext[Any, Any], params: _TaskIdParams) -> dict[str, Any]:
    """``tasks/get``, with ``inputRequests`` attached while the task is paused.

    Returned as a dict rather than a typed ``GetTaskResult`` because the SDK's
    model has no field for the map -- it is the SEP-1686 shape, and SEP-2663's
    ``inputRequests`` has nowhere to live on it. Without the map a client knows
    only that some input is wanted, not WHICH, so a relay in front of this server
    has nothing meaningful to forward.

    Everything else stays on the older design deliberately: the payload lives
    behind ``tasks/result`` rather than inlined here, so what the relay driver
    exercises is Hangar bridging the two generations.
    """
    record = STORE.get(params.task_id)
    result = record.to_get_result().model_dump(by_alias=True, exclude_none=True)
    if record.status == "input_required":
        result["inputRequests"] = INPUT_REQUESTS
    return result


async def handle_tasks_result(ctx: ServerRequestContext[Any, Any], params: _TaskIdParams) -> t.CallToolResult:
    return STORE.result_payload(params.task_id)


async def handle_tasks_cancel(ctx: ServerRequestContext[Any, Any], params: _TaskIdParams) -> t.CancelTaskResult:
    return STORE.cancel(params.task_id).to_cancel_result()


async def handle_tasks_list(ctx: ServerRequestContext[Any, Any], params: _ListParams) -> t.ListTasksResult:
    return t.ListTasksResult(tasks=[record.to_task() for record in STORE.list()])


async def handle_tasks_update(ctx: ServerRequestContext[Any, Any], params: _TaskIdParams) -> t.GetTaskResult:
    return STORE.resolve_input(params.task_id).to_get_result()


def wire_task_surface(server: MCPServer) -> None:
    """Attach the middleware and the ``tasks/*`` handlers to *server*.

    Reaches into ``_lowlevel_server`` deliberately: on b2 there is no public
    high-level API for either, which is the whole point of this example.
    """
    lowlevel = server._lowlevel_server
    lowlevel.middleware.append(task_emitting_middleware)
    lowlevel.add_request_handler("tasks/get", _TaskIdParams, handle_tasks_get)
    lowlevel.add_request_handler("tasks/result", _TaskIdParams, handle_tasks_result)
    lowlevel.add_request_handler("tasks/cancel", _TaskIdParams, handle_tasks_cancel)
    lowlevel.add_request_handler("tasks/list", _ListParams, handle_tasks_list)
    lowlevel.add_request_handler("tasks/update", _TaskIdParams, handle_tasks_update)


wire_task_surface(mcp)


def main() -> None:
    asyncio.run(
        mcp.run_streamable_http_async(
            host=HOST,
            port=PORT,
            streamable_http_path=STREAMABLE_HTTP_PATH,
        )
    )


if __name__ == "__main__":
    main()
