"""What the ``approval_gate.check`` span says the gate decided (#1274).

``_gate_approval`` wrote ``approval.result=not_required`` for two outcomes that
were anything but. A hold a human granted, and the dispatch re-check then
confirmed, read as if no approval had been needed. An L7 ``requireApproval``
verdict with no approval gate to ask read the same way, although the aggregate
refuses that call at dispatch.

Every terminal path of the gate is driven through ``_gate_approval``. Where a
decision can be made, it comes from a real ``ApprovalGateService`` over an
in-memory store and the real hold registry: the delivery channel answers
through ``ApprovalGateService.resolve``, which is what the REST resolve route
calls. The timeout is the service's own with a zero-second window, and the gate
error is the service failing to persist the request. Only one path uses a
stand-in gate: a refusal with no error code, the ``ApprovalDenied`` fallback,
which the real service never returns.

The span label is an annotation, not a decision. ``test_the_gate_decides_*``
pins what the gate returns on every path -- whether it refuses, with which
error type, and whether the approval id dispatch reads is set -- and runs
without the SDK. The span tests read the label from a real SDK exporter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.commands.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalResult, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.server.tools.batch.executor import BatchExecutor, _approval_loop_local, _CallPipeline
from mcp_hangar.server.tools.batch.models import CallResult, CallSpec

SERVER = "ledger"
TOOL = "transfer"
ARGS = {"amount": 10, "to": "acct-2"}

_L7_REQUIRES_APPROVAL = L7Policy.from_dict(
    {"tools": {"requireApproval": [TOOL]}, "defaultAction": "Allow", "mode": "Enforce"}
)


# --- collaborators ----------------------------------------------------------


class _Store:
    """In-memory approval repository, as the other approval tests use."""

    def __init__(self) -> None:
        self._rows: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self._rows[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._rows.get(approval_id)

    async def update_state(self, approval_id, state, decided_by, decided_at, reason) -> None:
        row = self._rows[approval_id]
        row.state, row.decided_by, row.decided_at, row.reason = state, decided_by, decided_at, reason


class _UnwritableStore(_Store):
    async def save(self, request: ApprovalRequest) -> None:
        raise OSError("approval store unavailable")


class _Approver:
    """The delivery channel, with a human at the far end who answers at once.

    ``approve=None`` never answers, so the hold runs to its timeout.
    ``during_hold`` runs before the answer -- an operator changing the world
    while the call waits.
    """

    def __init__(self, approve: bool | None, during_hold: Callable[[], None] | None = None) -> None:
        self.approve = approve
        self.during_hold = during_hold
        self.service: ApprovalGateService | None = None
        self.approval_id: str | None = None

    async def send(self, request: ApprovalRequest) -> None:
        self.approval_id = request.approval_id
        if self.during_hold is not None:
            self.during_hold()
        if self.approve is not None and self.service is not None:
            await self.service.resolve(request.approval_id, self.approve, "approver-1", None if self.approve else "no")


class _MalformedGate:
    """A refusal with no error code: the fallback the real service never takes."""

    async def check(self, **_kwargs: Any) -> ApprovalResult:
        return ApprovalResult(approved=False, approval_id="ap-malformed")


def _service(approver: _Approver, store: _Store | None = None) -> ApprovalGateService:
    service = ApprovalGateService(
        repository=store or _Store(),
        hold_registry=ApprovalHoldRegistry(),
        event_bus=Mock(),
        delivery=approver,
    )
    approver.service = service
    return service


def _servers(l7: L7Policy | None = None) -> InMemoryMcpServerRepository:
    server = McpServer(mcp_server_id=SERVER, mode="remote", endpoint="https://upstream.example/mcp")
    server.set_l7_policy(l7)
    servers = InMemoryMcpServerRepository()
    servers.add(SERVER, server)
    return servers


def _hold_for_a_human(timeout: int = 300) -> None:
    get_tool_access_resolver().set_mcp_server_policy(
        SERVER, ToolAccessPolicy(approval_list=(TOOL,), approval_timeout_seconds=timeout)
    )


def _deny_the_tool() -> None:
    get_tool_access_resolver().set_mcp_server_policy(SERVER, ToolAccessPolicy(deny_list=(TOOL,)))


# --- the terminal paths ------------------------------------------------------


@dataclass(frozen=True)
class Path:
    arrange: Callable[[], tuple[Any, _Approver | None]]
    #: None when the gate lets the call through, else the refusal's error type.
    refused_with: str | None
    #: Whether the approval id dispatch reads is left set, and to the granted id.
    approval_id_set: bool
    label: str


def _no_hold() -> tuple[Any, _Approver | None]:
    approver = _Approver(approve=True)
    return SimpleNamespace(approval_gate=_service(approver), repository=_servers()), approver


def _approval_list_without_gate() -> tuple[Any, _Approver | None]:
    # Unreachable in a booted server: the startup reachability check refuses an
    # approval_list with no gate. Pinned so the label and pass are not changed.
    _hold_for_a_human()
    return SimpleNamespace(approval_gate=None, repository=_servers()), None


def _approved() -> tuple[Any, _Approver | None]:
    _hold_for_a_human()
    approver = _Approver(approve=True)
    return SimpleNamespace(approval_gate=_service(approver), repository=_servers()), approver


def _approved_by_l7() -> tuple[Any, _Approver | None]:
    approver = _Approver(approve=True)
    return SimpleNamespace(approval_gate=_service(approver), repository=_servers(_L7_REQUIRES_APPROVAL)), approver


def _l7_without_gate() -> tuple[Any, _Approver | None]:
    return SimpleNamespace(approval_gate=None, repository=_servers(_L7_REQUIRES_APPROVAL)), None


def _denied() -> tuple[Any, _Approver | None]:
    _hold_for_a_human()
    approver = _Approver(approve=False)
    return SimpleNamespace(approval_gate=_service(approver), repository=_servers()), approver


def _timed_out() -> tuple[Any, _Approver | None]:
    _hold_for_a_human(timeout=0)
    approver = _Approver(approve=None)
    return SimpleNamespace(approval_gate=_service(approver), repository=_servers()), approver


def _gate_error() -> tuple[Any, _Approver | None]:
    _hold_for_a_human()
    approver = _Approver(approve=True)
    return SimpleNamespace(approval_gate=_service(approver, _UnwritableStore()), repository=_servers()), approver


def _denied_without_a_code() -> tuple[Any, _Approver | None]:
    _hold_for_a_human()
    return SimpleNamespace(approval_gate=_MalformedGate(), repository=_servers()), None


def _revalidation_failed() -> tuple[Any, _Approver | None]:
    # Approved, but the tool was denied by a config reload during the hold.
    _hold_for_a_human()
    approver = _Approver(approve=True, during_hold=_deny_the_tool)
    return SimpleNamespace(approval_gate=_service(approver), repository=_servers()), approver


PATHS: dict[str, Path] = {
    "no_hold": Path(_no_hold, None, False, "not_required"),
    "approval_list_without_gate": Path(_approval_list_without_gate, None, False, "not_required"),
    "approved": Path(_approved, None, True, "approved"),
    "approved_by_l7_requirement": Path(_approved_by_l7, None, True, "approved"),
    "l7_requirement_without_gate": Path(_l7_without_gate, None, False, "unavailable"),
    "denied": Path(_denied, "approval_denied", False, "approval_denied"),
    "timed_out": Path(_timed_out, "approval_timeout", False, "approval_timeout"),
    "gate_error": Path(_gate_error, "ApprovalGateError", False, "ApprovalGateError"),
    "denied_without_a_code": Path(_denied_without_a_code, "ApprovalDenied", False, "ApprovalDenied"),
    "revalidation_failed": Path(_revalidation_failed, "ToolAccessDenied", True, "revalidation_failed"),
}


@pytest.fixture(autouse=True)
def _clean_state():
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    _approval_loop_local.approval_id = None
    yield
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    _approval_loop_local.approval_id = None


@pytest.fixture()
def sdk():
    """A local TracerProvider + InMemorySpanExporter, never registered globally."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter, provider.get_tracer("test-1274")
    exporter.clear()


def _gate(ctx: Any, tracer: Any) -> CallResult | None:
    now = time.perf_counter()
    pipeline = _CallPipeline(
        call=CallSpec(index=0, call_id="c-1274", mcp_server=SERVER, tool=TOOL, arguments=dict(ARGS)),
        ctx=ctx,
        call_start=now,
        cancel_event=threading.Event(),
        global_timeout=30.0,
        batch_start_time=now,
        caller_tenant_id=None,
        resolver=get_tool_access_resolver(),
        proj_registry=get_tool_projection_registry(),
        tracer=tracer,
    )
    pipeline.target_server_id = SERVER
    return BatchExecutor()._gate_approval(pipeline)


def _approval_label(exporter: Any) -> Any:
    [span] = [s for s in exporter.get_finished_spans() if s.name == "approval_gate.check"]
    return span.attributes.get("approval.result")


# --- the decision: identical before and after --------------------------------


@pytest.mark.parametrize("name", PATHS)
def test_the_gate_decides_what_it_decided_before(name: str) -> None:
    """Refuse or pass, with which error, and the id dispatch carries."""
    from opentelemetry.trace import NoOpTracer

    path = PATHS[name]
    ctx, approver = path.arrange()

    result = _gate(ctx, NoOpTracer())

    if path.refused_with is None:
        assert result is None, result
    else:
        assert result is not None and result.success is False
        assert result.error_type == path.refused_with
    carried = getattr(_approval_loop_local, "approval_id", None)
    if path.approval_id_set:
        assert approver is not None and carried is not None and carried == approver.approval_id
    else:
        assert carried is None


# --- the label ----------------------------------------------------------------


@pytest.mark.otel_sdk
@pytest.mark.parametrize("name", PATHS)
def test_the_span_names_what_the_gate_decided(name: str, sdk) -> None:
    exporter, tracer = sdk
    path = PATHS[name]
    ctx, _ = path.arrange()

    _gate(ctx, tracer)

    assert _approval_label(exporter) == path.label


# --- end to end through the executor ------------------------------------------


def _executor_ctx(*, approval_gate: Any, servers: InMemoryMcpServerRepository, command_bus: Any) -> Mock:
    """A ready, healthy server; every gate but the approval gate open."""
    ctx = Mock()
    ctx.approval_gate = approval_gate
    ctx.repository = servers
    ctx.command_bus = command_bus
    ctx.governed_task_store = None
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False))
    )
    ctx.mcp_server_exists.return_value = True
    return ctx


def _execute(ctx: Any, tracer: Any) -> CallResult:
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
        patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=tracer),
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        batch = BatchExecutor().execute(
            batch_id="b-1274",
            calls=[CallSpec(index=0, call_id="c-1274", mcp_server=SERVER, tool=TOOL, arguments=dict(ARGS))],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    return batch.results[0]


@pytest.mark.otel_sdk
def test_an_l7_requirement_without_a_gate_is_unavailable_and_still_refused(sdk) -> None:
    """The real aggregate, behind the real command bus, refuses the dispatch."""
    exporter, tracer = sdk
    servers = _servers(_L7_REQUIRES_APPROVAL)
    bus = CommandBus()
    bus.register(InvokeToolCommand, InvokeToolHandler(servers, Mock()))

    result = _execute(_executor_ctx(approval_gate=None, servers=servers, command_bus=bus), tracer)

    assert (result.success, result.error_type, result.error) == (
        False,
        "EgressPolicyApprovalRequiredError",
        "Tool call requires approval",
    )
    assert _approval_label(exporter) == "unavailable"


@pytest.mark.otel_sdk
def test_an_approved_hold_is_dispatched_with_its_approval_and_says_so(sdk) -> None:
    exporter, tracer = sdk
    _hold_for_a_human()
    approver = _Approver(approve=True)
    bus = Mock()
    bus.send.return_value = {"ok": True}

    result = _execute(_executor_ctx(approval_gate=_service(approver), servers=_servers(), command_bus=bus), tracer)

    assert result.success is True, result.error
    [command] = [c.args[0] for c in bus.send.call_args_list if isinstance(c.args[0], InvokeToolCommand)]
    assert approver.approval_id is not None and command.l7_approval_id == approver.approval_id
    assert _approval_label(exporter) == "approved"


def test_the_approved_path_is_a_grant_a_human_made() -> None:
    """The approved row is a real grant on the record, not a pass the service fabricated."""
    from opentelemetry.trace import NoOpTracer

    ctx, approver = _approved()

    assert _gate(ctx, NoOpTracer()) is None
    assert approver is not None and approver.approval_id is not None
    assert ctx.approval_gate._repository._rows[approver.approval_id].state is ApprovalState.APPROVED
