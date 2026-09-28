"""A group call's audit record names the group it was addressed to (#1594).

The invoked ``McpServer`` raises ``ToolInvocationCompleted``/``Failed``, and for a
group call that aggregate is the selected member. The audit record took its
``mcp.server.id`` from there, so it named the member, while ADR-029 keys audit
on the logical target and the spans already said the group (#1286). An auditor
asking what was called on group G had to know G's membership at each call.

Now ``InvokeToolCommand`` carries ``logical_target`` into the aggregate, both
invocation events store it, and the record has the pair: ``mcp.server.id`` is
the logical target (falling back to the invoked server when it is empty) and
``hangar.route.backend`` the member. A standalone call carries the same value
in both. A refusal (#1582) names its backend once one was chosen. The four
compliance formats carry the same pair.

Naming: neutral placeholders only (pool, member-a, member-b, solo).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.application.services.validator_pipeline import ValidatorPipeline
from mcp_hangar.compliance.cef_exporter import CEFExporter
from mcp_hangar.compliance.jsonlines_exporter import JSONLinesExporter
from mcp_hangar.compliance.leef_exporter import LEEFExporter
from mcp_hangar.compliance.syslog_exporter import SyslogExporter
from mcp_hangar.domain.contracts.validator import ValidationResult
from mcp_hangar.domain.events import ToolCallRefused, ToolInvocationCompleted, ToolInvocationFailed
from mcp_hangar.domain.exceptions import EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer
from mcp_hangar.observability.conventions import McpServer as McpServerAttr
from mcp_hangar.observability.conventions import Route
from tests.unit import test_route_decisions_on_spans as routes

_GROUP, _A, _B, _SOLO, _TOOL = routes._GROUP, routes._A, routes._B, routes._SOLO, routes._TOOL

world = routes.world
_reset_singletons = routes._reset_singletons


def _record(event: object) -> dict[str, Any]:
    """The attributes the production OTLP audit exporter writes for *event*."""
    exporter = OTLPAuditExporter()
    with patch.object(exporter, "_emit_log_record") as emit:
        OTLPAuditEventHandler(audit_exporter=exporter).handle(event)
    [call] = emit.call_args_list
    return dict(call.args[0])


def _pair(record: dict[str, Any]) -> tuple[Any, Any]:
    return record.get(McpServerAttr.ID), record.get(Route.BACKEND)


# -- the command reaches the events ----------------------------------------------------


def _ready_server(response: dict[str, Any]) -> McpServer:
    server = McpServer(mcp_server_id=_A, mode="subprocess", command=["unused"])
    client = MagicMock()
    client.is_alive.return_value = True
    client.call.return_value = response
    with server._lock:
        server._state = McpServerState.READY
        server._client = client
    server._tools.update_from_list([{"name": _TOOL, "description": _TOOL, "inputSchema": {}}])
    return server


def _handle(server: McpServer, **command: Any) -> list[object]:
    repository = MagicMock()
    repository.get.return_value = server
    bus = MagicMock()
    handler = InvokeToolHandler(repository, bus)
    try:
        handler.handle(InvokeToolCommand(mcp_server_id=_A, tool_name=_TOOL, **command))
    except Exception:  # noqa: BLE001 -- the failure case raises after its event is published
        pass
    return [event for c in bus.publish_aggregate_events.call_args_list for event in c.args[2]]


class TestTheCommandCarriesTheLogicalTarget:
    def test_a_completed_group_call_stores_the_group(self) -> None:
        events = _handle(_ready_server({"result": {"content": []}}), logical_target=_GROUP)

        [done] = [e for e in events if isinstance(e, ToolInvocationCompleted)]
        assert (done.mcp_server_id, done.logical_target) == (_A, _GROUP)

    def test_a_failed_group_call_stores_the_group(self) -> None:
        events = _handle(_ready_server({"error": {"code": -32000, "message": "boom"}}), logical_target=_GROUP)

        [failed] = [e for e in events if isinstance(e, ToolInvocationFailed)]
        assert (failed.mcp_server_id, failed.logical_target) == (_A, _GROUP)

    def test_a_command_without_one_stores_none(self) -> None:
        events = _handle(_ready_server({"result": {"content": []}}))

        [done] = [e for e in events if isinstance(e, ToolInvocationCompleted)]
        assert done.logical_target == ""

    def test_the_default_is_empty(self) -> None:
        assert InvokeToolCommand(mcp_server_id=_A, tool_name=_TOOL).logical_target == ""


def _invocations(world: Any) -> list[InvokeToolCommand]:
    return [
        c.args[0] for c in world.context.command_bus.send.call_args_list if isinstance(c.args[0], InvokeToolCommand)
    ]


class TestTheExecutorNamesWhatTheCallerNamed:
    def test_a_group_call_dispatches_to_the_member_on_behalf_of_the_group(self, world) -> None:
        assert routes._call(_GROUP).success is True

        [command] = _invocations(world)
        assert (command.mcp_server_id, command.logical_target) == (_A, _GROUP)

    def test_a_standalone_call_names_itself(self, world) -> None:
        assert routes._call(_SOLO).success is True

        [command] = _invocations(world)
        assert (command.mcp_server_id, command.logical_target) == (_SOLO, _SOLO)


# -- refusals (#1582) name their backend once one was chosen --------------------------


def _refused(world: Any) -> ToolCallRefused:
    [event] = [
        c.args[0] for c in world.context.event_bus.publish.call_args_list if isinstance(c.args[0], ToolCallRefused)
    ]
    return event


class TestARefusedGroupCall:
    def test_a_gate_after_the_selection_names_the_member(self, world) -> None:
        with patch.object(ValidatorPipeline, "execute", return_value=ValidationResult.deny("no")):
            assert routes._call(_GROUP).success is False

        event = _refused(world)
        assert (event.gate, event.mcp_server_id, event.route_backend) == ("validators", _GROUP, _A)
        assert _pair(_record(event)) == (_GROUP, _A)

    def test_an_l7_refusal_at_dispatch_names_the_member(self, world) -> None:
        def send(command: object) -> object:
            if isinstance(command, InvokeToolCommand):
                raise EgressPolicyDeniedError(_A, _TOOL, "rule")
            return {"ok": True}

        world.context.command_bus.send.side_effect = send

        assert routes._call(_GROUP).error_type == "EgressPolicyDeniedError"

        event = _refused(world)
        assert (event.mcp_server_id, event.route_backend) == (_GROUP, _A)
        assert _pair(_record(event)) == (_GROUP, _A)

    def test_no_member_to_select_names_no_backend(self, world) -> None:
        for member_id in (_A, _B):
            world.group.get_member(member_id).in_rotation = False

        assert routes._call(_GROUP).error_type == "NoAvailableMemberError"

        event = _refused(world)
        assert (event.gate, event.route_backend) == ("resolve_target", None)
        assert _pair(_record(event)) == (_GROUP, None)

    def test_a_standalone_refusal_names_the_server_twice(self, world) -> None:
        with patch.object(ValidatorPipeline, "execute", return_value=ValidationResult.deny("no")):
            assert routes._call(_SOLO).success is False

        assert _pair(_record(_refused(world))) == (_SOLO, _SOLO)


# -- the audit handler's mapping ---------------------------------------------------------


_EVENTS: dict[str, Callable[..., object]] = {
    "completed": lambda **kw: ToolInvocationCompleted(tool_name=_TOOL, duration_ms=1.0, **kw),
    "failed": lambda **kw: ToolInvocationFailed(tool_name=_TOOL, duration_ms=1.0, error_type="tool_error", **kw),
}


@pytest.mark.parametrize("kind", sorted(_EVENTS))
class TestTheAuditRecord:
    def test_a_group_call_names_the_group_and_the_member(self, kind: str) -> None:
        record = _record(_EVENTS[kind](mcp_server_id=_A, logical_target=_GROUP))

        assert _pair(record) == (_GROUP, _A)

    def test_a_standalone_call_names_the_server_twice(self, kind: str) -> None:
        record = _record(_EVENTS[kind](mcp_server_id=_SOLO, logical_target=_SOLO))

        assert _pair(record) == (_SOLO, _SOLO)

    def test_an_event_without_a_logical_target_falls_back_to_the_server(self, kind: str) -> None:
        """An event persisted before #1594, or a call made outside the executor."""
        record = _record(_EVENTS[kind](mcp_server_id=_SOLO))

        assert _pair(record) == (_SOLO, _SOLO)


# -- the compliance formats ---------------------------------------------------------------


def _json_backend(line: str) -> tuple[str | None, str | None]:
    payload = json.loads(line)
    return payload.get("provider_id"), payload.get("route_backend")


#: Each format, and where its line holds the (target, backend) pair.
_FORMATS: dict[str, tuple[Callable[..., Any], Callable[[str, str], str], Callable[[str], str]]] = {
    "cef": (
        CEFExporter,
        lambda target, _b: f"cs1={target} cs1Label=ProviderID",
        lambda b: f"flexString1={b} flexString1Label=RouteBackend",
    ),
    "leef": (LEEFExporter, lambda target, _b: f"\tsrc={target}", lambda b: f"\trouteBackend={b}"),
    "syslog": (SyslogExporter, lambda target, _b: f'provider="{target}"', lambda b: f'routeBackend="{b}"'),
}


def _export(exporter: Callable[..., Any], event: object) -> str:
    lines: list[str] = []
    OTLPAuditEventHandler(audit_exporter=exporter(output_fn=lines.append)).handle(event)
    [line] = lines
    return line


@pytest.mark.parametrize("fmt", sorted([*_FORMATS, "jsonlines"]))
@pytest.mark.parametrize(
    ("event", "target", "backend"),
    [
        (ToolInvocationCompleted(mcp_server_id=_A, tool_name=_TOOL, logical_target=_GROUP), _GROUP, _A),
        (ToolInvocationFailed(mcp_server_id=_A, tool_name=_TOOL, logical_target=_GROUP), _GROUP, _A),
        (ToolInvocationCompleted(mcp_server_id=_SOLO, tool_name=_TOOL, logical_target=_SOLO), _SOLO, _SOLO),
        (ToolInvocationCompleted(mcp_server_id=_SOLO, tool_name=_TOOL), _SOLO, _SOLO),
        (ToolCallRefused(mcp_server_id=_GROUP, tool_name=_TOOL, gate="validators", route_backend=_A), _GROUP, _A),
        (ToolCallRefused(mcp_server_id=_GROUP, tool_name=_TOOL, gate="resolve_target"), _GROUP, None),
    ],
    ids=["group-completed", "group-failed", "standalone", "no-logical-target", "refused-group", "refused-no-backend"],
)
def test_every_compliance_format_carries_the_pair(fmt: str, event: object, target: str, backend: str | None) -> None:
    if fmt == "jsonlines":
        assert _json_backend(_export(JSONLinesExporter, event)) == (target, backend)
        return
    exporter, target_marker, backend_marker = _FORMATS[fmt]
    line = _export(exporter, event)

    assert target_marker(target, "") in line
    if backend is None:
        assert "outeBackend" not in line and "flexString1" not in line
    else:
        assert backend_marker(backend) in line


# -- replay ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "dropped", "default"),
    [
        (ToolInvocationCompleted(mcp_server_id=_A, tool_name=_TOOL, logical_target=_GROUP), "logical_target", ""),
        (ToolInvocationFailed(mcp_server_id=_A, tool_name=_TOOL, logical_target=_GROUP), "logical_target", ""),
        (ToolCallRefused(mcp_server_id=_GROUP, tool_name=_TOOL, route_backend=_A), "route_backend", None),
    ],
    ids=["completed", "failed", "refused"],
)
def test_an_event_persisted_before_the_field_existed_replays(event: object, dropped: str, default: object) -> None:
    serializer = EventSerializer()
    type_name, data = serializer.serialize(event)  # type: ignore[arg-type]
    payload = json.loads(data)
    assert payload.pop(dropped) is not None
    old_shaped = json.dumps(payload)

    restored = serializer.deserialize(type_name, old_shaped)

    assert type(restored) is type(event)
    assert getattr(restored, dropped) == default
    assert restored.event_id == event.event_id  # type: ignore[attr-defined]
    if dropped == "logical_target":  # and its record falls back to the member
        assert _pair(_record(restored)) == (_A, _A)
