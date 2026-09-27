"""A governed task's follow-ups are bounded spans linked to the call that created it (#1281).

`tasks/get`, `tasks/cancel` and `tasks/update` each open one `task_relay.<op>`
span. It is a child of the request's SERVER span, never a new root (ADR-029
s3, Alternative 2), so it sits in the follow-up's own trace, and it carries
exactly one link to the `batch.call.<tool>` span that created the task
(ADR-029 s2, s8). The link is added only once the caller is shown to own the
task. A refusal and `not_found` stay UNSET; only a failed relay ends ERROR
(ADR-029 s5). No task id reaches any span.

The handlers are the real ones against a real `GovernedTaskStore` and a real
OpenTelemetry SDK; the SERVER span the mcp SDK middleware opens is stood in
for by one opened the same way. The served-app test over streamable HTTP is
`tests/integration/test_task_follow_ups_link_to_origin_on_the_served_app.py`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar._sdk_compat import McpError, make_mcp_error
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import PrincipalType
from mcp_hangar.fastmcp_server.task_relay_handlers import register_task_relay_handlers
from mcp_hangar.infrastructure.observability import task_relay_spans
from mcp_hangar.observability.conventions import GenAI, McpServer, TaskRelay
from mcp_hangar.observability.tracing import current_traceparent
from mcp_hangar.server.tools.batch import executor, relay_seam
from mcp_hangar.server.tools.batch.models import RelayCapture
from mcp_hangar.tasks_wire import EXTENSION_ID

pytestmark = pytest.mark.otel_sdk

#: A task id nothing else produces, so any copy of it on a span is found.
TASK = "TASKID-CANARY-1281"
SERVER = "job-a"
GROUP = "job-pool"
TOOL = "job"
TENANT, OWNER = "tenant-a", "alice"
ERROR_TYPE = "error.type"


class _Low:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def add_request_handler(self, method: str, _params: Any, handler: Any) -> None:
        self.handlers[method] = handler


class _Router:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    def __call__(self, _server: str, method: str, _params: dict[str, Any], _timeout: float) -> Any:
        value = self.responses.get(method)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def otel() -> Iterator[SimpleNamespace]:
    """A local provider, never registered globally, behind Hangar's own tracer wrapper."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    hangar = _TextFreeTracer(provider.get_tracer("hangar"))
    with patch.object(task_relay_spans, "get_tracer", return_value=hangar):
        yield SimpleNamespace(exporter=exporter, sdk=provider.get_tracer("mcp-sdk"), hangar=hangar)


def _origin(otel: SimpleNamespace) -> tuple[str, Any]:
    """A finished `batch.call.<tool>` span, and its traceparent as the executor records it."""
    with otel.hangar.start_as_current_span(f"batch.call.{TOOL}") as span:
        origin = current_traceparent()
    assert origin is not None
    return origin, span.get_span_context()


@contextmanager
def _as(tenant: str, principal: str) -> Iterator[None]:
    caller = CallerIdentity(user_id=principal, agent_id=None, session_id=None, principal_type="user", tenant_id=tenant)
    token = identity_context_var.set(IdentityContext(caller=caller))
    try:
        yield
    finally:
        identity_context_var.reset(token)


def _register(store: GovernedTaskStore, origin: str | None) -> None:
    with _as(TENANT, OWNER):
        task = store.mint_from_upstream(_task())
        store.relay_and_govern(
            target_server_id=SERVER,
            task=task,
            expected_owner=TaskOwner(TENANT, OWNER),
            correlation_id="call-1",
            mcp_server_id=GROUP,
            tool_name=TOOL,
            origin_traceparent=origin,
        )


def _task(status: str = "working", **extra: Any) -> dict[str, Any]:
    return {
        "taskId": TASK,
        "status": status,
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
        **extra,
    }


def _ctx(principal: str = OWNER, tenant: str = TENANT, *, version: str = "2026-07-28") -> Any:
    auth = SimpleNamespace(
        principal=SimpleNamespace(
            is_anonymous=lambda: False, id=SimpleNamespace(value=principal), type=PrincipalType.USER, tenant_id=tenant
        )
    )
    return SimpleNamespace(
        request=SimpleNamespace(state=SimpleNamespace(auth=auth), headers={"mcp-name": TASK}),
        session=SimpleNamespace(
            protocol_version=version,
            client_params=SimpleNamespace(capabilities=SimpleNamespace(extensions={EXTENSION_ID: {}})),
        ),
    )


async def _follow_up(
    otel: SimpleNamespace, store: GovernedTaskStore, method: str, responses: dict[str, Any], **ctx: Any
) -> tuple[Any, Any, BaseException | None]:
    """Run one follow-up under a SERVER span opened as the mcp SDK opens it: its span, the SERVER span, the raise."""
    from opentelemetry.trace import SpanKind

    low = _Low()
    register_task_relay_handlers(SimpleNamespace(_mcp_server=low), store, TaskConsentGate(), _Router(responses))
    params = SimpleNamespace(task_id=TASK, input_responses={"r1": {"ok": True}}, model_dump=lambda **_: {})
    raised: BaseException | None = None
    with otel.sdk.start_as_current_span(
        method, kind=SpanKind.SERVER, record_exception=False, set_status_on_exception=False
    ) as server:
        try:
            await low.handlers[method](_ctx(**ctx), params)
        except McpError as error:
            raised = error
    [span] = [s for s in otel.exporter.get_finished_spans() if s.name == task_relay_spans.SPAN_PREFIX + method[6:]]
    return span, server, raised


def _assert_child_of_server(span: Any, server: Any) -> None:
    assert span.parent is not None and span.parent.span_id == server.get_span_context().span_id
    assert span.context.trace_id == server.get_span_context().trace_id


def _assert_linked_to(span: Any, origin_ctx: Any) -> None:
    assert [(link.context.trace_id, link.context.span_id) for link in span.links] == [
        (origin_ctx.trace_id, origin_ctx.span_id)
    ]
    assert span.context.trace_id != origin_ctx.trace_id


def _assert_no_task_id(otel: SimpleNamespace) -> None:
    for span in otel.exporter.get_finished_spans():
        values = [span.name, span.status.description or "", *map(str, (span.attributes or {}).values())]
        for event in span.events:
            values += [event.name, *map(str, (event.attributes or {}).values())]
        for link in span.links:
            values += list(map(str, (link.attributes or {}).values()))
        assert not [v for v in values if TASK in v], span.name


def _status(span: Any) -> str:
    return str(span.status.status_code.name)


@pytest.fixture
def allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "current_tool_access_refusal", lambda *a, **k: None)


@pytest.fixture
def withdrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "current_tool_access_refusal", lambda *a, **k: ("withdrawn", "ToolWithdrawnError"))


# -- tasks/get ---------------------------------------------------------------


async def test_a_served_poll_is_a_child_of_its_request_linked_once_to_the_creating_call(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)

    span, server, raised = await _follow_up(otel, store, "tasks/get", {"tasks/get": {"result": _task()}})

    assert raised is None
    _assert_child_of_server(span, server)
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.SERVED
    assert span.attributes[McpServer.ID] == GROUP
    assert span.attributes[GenAI.TOOL_NAME] == TOOL
    assert ERROR_TYPE not in span.attributes
    assert _status(span) == "UNSET"
    _assert_no_task_id(otel)


async def test_a_poll_the_upstream_failed_serves_the_snapshot_and_ends_error(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)

    span, _server, raised = await _follow_up(
        otel, store, "tasks/get", {"tasks/get": {"error": {"code": -32000, "message": TASK}}}
    )

    assert raised is None
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.UPSTREAM_ERROR
    assert span.attributes[ERROR_TYPE] == "-32000"
    assert _status(span) == "ERROR"
    assert not span.status.description
    _assert_no_task_id(otel)


async def test_a_poll_refused_for_tool_access_stays_unset(otel, withdrawn) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)

    span, server, raised = await _follow_up(
        otel, store, "tasks/get", {"tasks/get": {"result": _task("completed", result={"content": []})}}
    )

    assert isinstance(raised, McpError)
    _assert_child_of_server(span, server)
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.REFUSED
    assert span.attributes[ERROR_TYPE] == "ToolWithdrawnError"
    assert _status(span) == "UNSET"
    assert not [e for e in span.events if e.name == "exception"]
    _assert_no_task_id(otel)


async def test_an_unknown_task_is_not_found_with_no_link(otel) -> None:
    store = GovernedTaskStore()

    span, server, raised = await _follow_up(otel, store, "tasks/get", {})

    assert isinstance(raised, McpError)
    _assert_child_of_server(span, server)
    assert list(span.links) == []
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.NOT_FOUND
    assert span.attributes[ERROR_TYPE] == "-32602"
    assert _status(span) == "UNSET"
    _assert_no_task_id(otel)


async def test_a_foreign_task_yields_no_link_and_none_of_its_entry(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, _origin_ctx = _origin(otel)
    _register(store, origin)

    span, _server, raised = await _follow_up(
        otel, store, "tasks/get", {"tasks/get": {"result": _task()}}, principal="bob", tenant="tenant-b"
    )

    assert isinstance(raised, McpError)
    assert list(span.links) == []
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.NOT_FOUND
    assert McpServer.ID not in span.attributes and GenAI.TOOL_NAME not in span.attributes
    assert _status(span) == "UNSET"


async def test_a_caller_the_ladder_refuses_gets_refused_with_no_link(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, _origin_ctx = _origin(otel)
    _register(store, origin)

    span, _server, raised = await _follow_up(otel, store, "tasks/get", {}, version="2025-11-25")

    assert isinstance(raised, McpError)
    assert list(span.links) == []
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.REFUSED
    assert span.attributes[ERROR_TYPE] == "-32601"
    assert _status(span) == "UNSET"


@pytest.mark.parametrize("origin", ["", "00-not-a-traceparent", "00-" + "0" * 32 + "-" + "0" * 16 + "-01"])
async def test_an_empty_or_malformed_origin_gives_no_link(otel, allowed, origin: str) -> None:
    store = GovernedTaskStore()
    _register(store, origin)

    span, server, raised = await _follow_up(otel, store, "tasks/get", {"tasks/get": {"result": _task()}})

    assert raised is None
    _assert_child_of_server(span, server)
    assert list(span.links) == []
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.SERVED


async def test_a_restarted_process_answers_not_found_with_no_link(otel, allowed) -> None:
    """The ledger is in memory: a new store, as a new process or another replica builds, holds nothing."""
    origin, _origin_ctx = _origin(otel)
    _register(GovernedTaskStore(), origin)

    span, _server, raised = await _follow_up(otel, GovernedTaskStore(), "tasks/get", {"tasks/get": {"result": {}}})

    assert isinstance(raised, McpError)
    assert list(span.links) == []
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.NOT_FOUND


async def test_a_relay_that_raises_ends_error_with_its_class_name(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)

    with pytest.raises(TimeoutError):
        await _follow_up(otel, store, "tasks/get", {"tasks/get": TimeoutError(TASK)})

    [span] = [s for s in otel.exporter.get_finished_spans() if s.name == "task_relay.get"]
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.ERROR
    assert span.attributes[ERROR_TYPE] == "TimeoutError"
    assert _status(span) == "ERROR" and not span.status.description
    _assert_no_task_id(otel)


# -- tasks/cancel ------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "outcome", "status", "error_type"),
    [
        ({"result": _task("cancelled")}, TaskRelay.CONFIRMED, "UNSET", None),
        ({"result": _task("working")}, TaskRelay.UNCONFIRMED, "UNSET", None),
        ({"error": {"code": -32000, "message": TASK}}, TaskRelay.UNCONFIRMED, "ERROR", "-32000"),
    ],
)
async def test_a_cancel_records_whether_the_upstream_confirmed_it(
    otel, answer: dict[str, Any], outcome: str, status: str, error_type: str | None
) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)

    span, server, raised = await _follow_up(otel, store, "tasks/cancel", {"tasks/cancel": answer})

    assert raised is None
    _assert_child_of_server(span, server)
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == outcome
    assert span.attributes.get(ERROR_TYPE) == error_type
    assert _status(span) == status
    _assert_no_task_id(otel)


async def test_a_foreign_cancel_yields_no_link(otel) -> None:
    store = GovernedTaskStore()
    origin, _origin_ctx = _origin(otel)
    _register(store, origin)

    span, _server, raised = await _follow_up(
        otel, store, "tasks/cancel", {"tasks/cancel": {"result": {}}}, principal="bob", tenant="tenant-b"
    )

    assert isinstance(raised, McpError)
    assert list(span.links) == []
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.NOT_FOUND
    assert _status(span) == "UNSET"


# -- tasks/update ------------------------------------------------------------


async def test_a_relayed_update_is_linked_and_unset(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)
    answers = {"tasks/get": {"result": _task("input_required")}, "tasks/update": {"result": _task()}}

    span, server, raised = await _follow_up(otel, store, "tasks/update", answers)

    assert raised is None
    _assert_child_of_server(span, server)
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.RELAYED
    assert _status(span) == "UNSET"
    _assert_no_task_id(otel)


async def test_an_update_the_upstream_refused_ends_error(otel, allowed) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)
    answers = {"tasks/get": {"result": _task()}, "tasks/update": {"error": {"code": -32000, "message": TASK}}}

    span, _server, raised = await _follow_up(otel, store, "tasks/update", answers)

    assert isinstance(raised, McpError)
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.ERROR
    assert span.attributes[ERROR_TYPE] == "-32000"
    assert _status(span) == "ERROR" and not span.status.description
    _assert_no_task_id(otel)


async def test_an_update_refused_for_tool_access_stays_unset(otel, withdrawn) -> None:
    store = GovernedTaskStore()
    origin, origin_ctx = _origin(otel)
    _register(store, origin)

    span, _server, raised = await _follow_up(otel, store, "tasks/update", {})

    assert isinstance(raised, McpError)
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == TaskRelay.REFUSED
    assert span.attributes[ERROR_TYPE] == "ToolWithdrawnError"
    assert _status(span) == "UNSET"


# -- Hangar's own cancel of a task nobody was handed -------------------------


@pytest.mark.parametrize(
    ("answer", "outcome", "status"),
    [
        ({"result": {}}, TaskRelay.CONFIRMED, "UNSET"),
        ({"error": {"code": -32000, "message": TASK}}, TaskRelay.UNCONFIRMED, "ERROR"),
        (ConnectionError(TASK), TaskRelay.ERROR, "ERROR"),
    ],
)
def test_the_unhanded_cancel_is_a_new_root_linked_to_the_refused_call(
    otel, monkeypatch: pytest.MonkeyPatch, answer: Any, outcome: str, status: str
) -> None:
    origin, origin_ctx = _origin(otel)
    done = threading.Event()

    def router(_server: str, _method: str, _params: dict[str, Any], _timeout: float) -> Any:
        try:
            if isinstance(answer, Exception):
                raise answer
            return answer
        finally:
            done.set()

    monkeypatch.setattr(relay_seam, "get_context", lambda: SimpleNamespace(task_upstream_router=router))
    capture = RelayCapture(
        identity=None,
        pin=None,
        target_server_id=SERVER,
        correlation_id="call-1",
        upstream={"task": _task()},
        logical_mcp_server=GROUP,
        tool=TOOL,
        origin_traceparent=origin,
    )

    # Called from inside a request span, as the seam is: the cancel must not inherit it.
    with otel.hangar.start_as_current_span("tools/call"):
        relay_seam._cancel_unhanded_task(capture)
    assert done.wait(5)
    for _ in range(100):
        spans = [s for s in otel.exporter.get_finished_spans() if s.name == task_relay_spans.UNHANDED_CANCEL_SPAN]
        if spans:
            break
        threading.Event().wait(0.02)

    [span] = spans
    assert span.parent is None
    _assert_linked_to(span, origin_ctx)
    assert span.attributes[TaskRelay.OUTCOME] == outcome
    assert span.attributes[McpServer.ID] == GROUP
    assert _status(span) == status
    _assert_no_task_id(otel)


# -- vocabulary --------------------------------------------------------------


def test_an_outcome_outside_the_closed_list_is_not_exported(otel) -> None:
    with pytest.raises(McpError):
        with task_relay_spans._follow_up_span("task_relay.get"):
            task_relay_spans.record_follow_up("task-" + TASK)
            raise make_mcp_error(-32602, "x")

    [span] = otel.exporter.get_finished_spans()
    assert TaskRelay.OUTCOME not in span.attributes
    assert {
        "served",
        "upstream_error",
        "confirmed",
        "unconfirmed",
        "relayed",
        "not_found",
        "refused",
        "error",
    } == TaskRelay.OUTCOMES
