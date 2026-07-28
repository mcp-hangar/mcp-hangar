"""SEP-2663 Tasks-extension wire models, vendored deliberately.

These duplicate shapes that ``mcp_types`` appears to provide. That duplication is
the point, and removing it would reintroduce the bug this module exists to fix.

## Why not just use ``mcp_types``

``mcp_types`` carries Tasks types, but they are the **SEP-1686** generation --
the design that 2026-07-28 replaced:

===================  ==============================  ==============================
                     ``mcp_types`` (SEP-1686)        SEP-2663 (this module)
===================  ==============================  ==============================
create result        nested ``{task: {...}}``        flat, ``resultType: "task"``
TTL field            ``ttl`` (required)              ``ttlMs`` (required, nullable)
poll hint            ``pollInterval``                ``pollIntervalMs``
``tasks/get`` ->     flat snapshot                   snapshot + inlined outcome
``tasks/result``     present                         removed (``-32601``)
``tasks/list``       present                         removed
``tasks/update``     absent                          required
``resultType``       on 12 result classes,           required on every result
                     none of them ``Task*``
===================  ==============================  ==============================

(``mcp_types`` does declare ``result_type`` -- on ``DiscoverResult``,
``CallToolResult``, ``ListToolsResult`` and nine others -- and ``ResultType`` is
``Literal["complete", "input_required"] | str``, so it would accept ``"task"``.
No ``Task*`` class declares it. That narrower claim is the one that matters here
and the one that survives being checked.)

The obvious assumption -- that those types are mid-migration and will grow into
the SEP-2663 shape -- is false, and acting on it cost us a released artifact.
Measured across ``mcp==2.0.0b2`` (14 Jul) and ``2.0.0rc1`` (27 Jul): the Tasks
surface is **unchanged** -- all 29 ``Task*`` classes field-for-field identical,
``ListTasksResult`` still present, ``UpdateTaskRequest`` still absent -- while the
module around them **was** edited in that window (``__init__.py``, ``_types.py``
and ``v2026_07_28/__init__.py`` all changed, ``SERVER_INFO_META_KEY`` arrived,
``DiscoverResult`` lost ``server_info``).

That is the point, and it is stronger than "nobody maintains this file". These
types are not stale by neglect; they are a **deliberately frozen region of a file
under active edit**. python-sdk#3005 says why in its own design: the extension
defines its **own** SEP-2663 models precisely *because* they are wire-incompatible
with what stayed behind. The types in ``mcp_types`` are a fossil of the removed
core feature, kept for the 2025-11-25 generation. They never evolve in place.

So capability-probing them (``hasattr(_t, "UpdateTaskRequest")``) can never flip,
and serving them on a 2026-07-28 connection hands the client a reply it cannot
parse. `2.0.0rc1` shipped exactly that.

## Rules for this module

* **Never import a ``mcp_types.Task*`` type here.** That import is the failure
  mode, not a convenience.
* **This is not a third dialect.** Field names, defaults and nullability track
  python-sdk#3005 so that when it merges, ``GovernedTaskStore`` plugs in as its
  backend and these models retire rather than fork.

## The one deliberate divergence

:class:`GetTaskResult` carries ``inputRequests``; python-sdk#3005's does not.
Its ``GetTaskResult(Task)`` declares no such field and inherits pydantic's
default ``extra="ignore"``, so a server's ``inputRequests`` map is silently
dropped on parse -- which breaks the in-task input loop that same PR documents
(``update_task`` answers "keys of the snapshot's ``inputRequests``" that the
client can no longer see). Hangar's mid-flight consent gate reads that map, so
dropping it would break consent. Declared explicitly here rather than left to
``extra``, so it survives ``model_dump()`` too.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializationInfo, SerializerFunctionWrapHandler, model_serializer

#: Extension id negotiated at initialize. A client that has not declared it is
#: told so with ``-32021`` rather than being served.
EXTENSION_ID = "io.modelcontextprotocol/tasks"

#: JSON-RPC code for "you did not declare the capability this method needs".
#: Defined in ``mcp_types.jsonrpc`` as ``MISSING_REQUIRED_CLIENT_CAPABILITY``;
#: restated here so this module has no reason to import from the fossil's
#: neighbourhood, and because the numeric code is the wire contract.
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021

#: JSON-RPC code for "a routing header contradicts, or is missing from, the
#: request it routes" (``mcp_types.jsonrpc.HEADER_MISMATCH``). Reused rather than
#: invented: the SDK already answers a bad ``Mcp-Method`` / ``Mcp-Name`` with it,
#: so a client's existing handling applies unchanged.
HEADER_MISMATCH = -32020

#: Wire name of the SEP-2243 routing header SEP-2663 mandates on ``tasks/*``.
MCP_NAME_HEADER = "mcp-name"

#: Methods this extension serves on the modern wire. `tasks/result` and
#: `tasks/list` are absent BY DESIGN -- SEP-2663 removes both -- and that absence
#: is asserted in tests, so re-adding one here is a deliberate act.
TASKS_METHODS: frozenset[str] = frozenset({"tasks/get", "tasks/update", "tasks/cancel"})

#: Methods SEP-2663 requires to carry ``Mcp-Name: <taskId>`` (via SEP-2243), so an
#: intermediary can route a poll to the instance holding the task without reading
#: the body. Public because the egress policy compiler consumes it as an
#: inspection-free L7 selector (operator#53).
NAME_BEARING_TASK_METHODS: frozenset[str] = TASKS_METHODS

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


class _WireModel(BaseModel):
    """Base for every model on this wire: camelCase out, either name in.

    ``populate_by_name`` matters for round-tripping: handlers build these from
    snake_case kwargs, while anything parsed off the wire arrives camelCase.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class _CarriesTtlMs(BaseModel):
    """Keeps ``ttlMs`` on the wire even when it is ``None``.

    The extension schema types it ``ttlMs: number | null`` -- **required but
    nullable**, which pydantic cannot express through a default alone: a plain
    ``ttl_ms: int | None = None`` is dropped by ``exclude_none`` serialization and
    by any consumer that treats "absent" and "null" differently. SEP-2663 does
    treat them differently: absent means the field was not implemented, null
    means "no TTL". Re-inserting it in a wrap serializer is the same fix
    python-sdk#3005 applies, for the same reason.
    """

    ttl_ms: int | None = None

    @model_serializer(mode="wrap")
    def _keep_ttl_ms(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo) -> dict[str, Any]:
        # `handler` is typed to return Any (it serves both dict and non-dict
        # modes); every model here is a BaseModel, so the dict branch is the only
        # reachable one.
        data: dict[str, Any] = handler(self)
        data.setdefault("ttlMs" if info.by_alias else "ttl_ms", self.ttl_ms)
        return data


class Task(_CarriesTtlMs, _WireModel):
    """A task snapshot as SEP-2663 puts it on the wire."""

    task_id: str
    status: TaskStatus
    status_message: str | None = None
    created_at: str
    last_updated_at: str
    ttl_ms: int | None = None
    poll_interval_ms: int | None = None


class CreateTaskResult(_CarriesTtlMs, _WireModel):
    """Response to an augmented ``tools/call``: ``Result & Task``, **flat**.

    The flatness is the incompatibility. ``mcp_types.CreateTaskResult`` nests the
    same data under a ``task`` key, so a SEP-2663 client reading ``taskId`` off
    the top level finds nothing -- it does not error, it just cannot see the task.
    """

    result_type: Literal["task"] = "task"
    task_id: str
    status: TaskStatus
    status_message: str | None = None
    created_at: str
    last_updated_at: str
    ttl_ms: int | None = None
    poll_interval_ms: int | None = None

    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class GetTaskResult(Task):
    """Response to ``tasks/get``: the snapshot with its outcome inlined.

    SEP-2663 folds what used to need a second ``tasks/result`` round trip into
    the poll response: ``result`` once the task ``completed`` (a tool result with
    ``isError: true`` included -- that is a completed task, not a failed one),
    ``error`` once it ``failed``, both ``None`` while it is still running.

    ``input_requests`` is the deliberate addition over python-sdk#3005 -- see the
    module docstring. Without it the mid-flight consent gate has nothing to gate.
    """

    result_type: Literal["complete"] = "complete"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = None

    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class EmptyResult(_WireModel):
    """The acknowledgement ``tasks/cancel`` and ``tasks/update`` return.

    Both change nothing by design. Cancellation is cooperative -- SEP-2663 lets a
    task reach a terminal status other than ``cancelled`` when the work finished
    first -- so the ack must not rewrite a status the upstream owns. ``resultType``
    is still required on it.
    """

    result_type: Literal["complete"] = "complete"

    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class GetTaskRequestParams(_WireModel):
    task_id: str


class CancelTaskRequestParams(_WireModel):
    task_id: str


class UpdateTaskRequestParams(_WireModel):
    """``tasks/update``: answers to the snapshot's ``inputRequests``.

    Keys that name an input request never issued are ignored rather than
    rejected, per SEP-2663 -- but a call carrying no answers at all is a
    malformed request, not an empty no-op, so ``input_responses`` is required.
    """

    task_id: str
    input_responses: dict[str, Any]


def missing_capability_error_data() -> dict[str, Any]:
    """The machine-readable payload that rides with ``-32021``.

    Sent to a client on the modern wire that never declared the extension. It is
    told *what to declare* rather than just refused, because unlike a legacy
    client it can fix this and retry -- which is why SEP-2663 splits the two
    cases (``-32021`` here, ``-32601`` for a connection that could never speak it).
    """
    return {"requiredCapabilities": {"extensions": {EXTENSION_ID: {}}}}


__all__ = [
    "CancelTaskRequestParams",
    "HEADER_MISMATCH",
    "MCP_NAME_HEADER",
    "CreateTaskResult",
    "EXTENSION_ID",
    "EmptyResult",
    "GetTaskRequestParams",
    "GetTaskResult",
    "MISSING_REQUIRED_CLIENT_CAPABILITY",
    "NAME_BEARING_TASK_METHODS",
    "TASKS_METHODS",
    "Task",
    "TaskStatus",
    "UpdateTaskRequestParams",
]
