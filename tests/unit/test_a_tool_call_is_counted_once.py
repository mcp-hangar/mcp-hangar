"""One tool call is one observation in the call metrics, counted through the real buses (#1299)."""

import contextlib
from collections import Counter
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest

from mcp_hangar import metrics as m
from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolRules
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.stream_ids import MCP_SERVER

CASES = [  # (upstream reply, tool, policy, error_type); the first four reach the upstream
    ({"result": {}}, "add", None, None),
    (OSError("gone"), "add", None, "OSError"),
    ({"error": {"code": -32602}}, "add", None, "-32602"),
    ({"result": {"isError": True}}, "add", None, "tool_error"),
    ({"result": {}}, "missing", None, "ToolNotFoundError"),
    ({"result": {}}, "add", L7Policy(tools=ToolRules(deny=("add",))), "EgressPolicyDeniedError"),
]


def _setup(reply, policy=None):
    server = McpServer(mcp_server_id=uuid4().hex, mode="subprocess", command=["echo"], l7_policy=policy)
    server.ensure_ready = Mock()
    upstream = Mock(side_effect=reply) if isinstance(reply, Exception) else Mock(return_value=reply)
    server._client = MagicMock(call=upstream)
    server._tools.update_from_list([{"name": "add"}])
    events = EventBus()
    events.subscribe_to_all(MetricsEventHandler().handle, kind=HandlerKind.EFFECT)
    bus = CommandBus()
    bus.register(InvokeToolCommand, InvokeToolHandler(Mock(get=Mock(return_value=server)), events))

    def send(tool):
        with contextlib.suppress(Exception):
            bus.send(InvokeToolCommand(mcp_server_id=server.mcp_server_id, tool_name=tool, arguments={}))

    return server, events, send


def _counts(sid):
    calls, errors = Counter(), Counter()
    for s in m.TOOL_CALLS_TOTAL.collect():
        calls[s.labels["status"]] += s.value * (s.labels["mcp_server"] == sid)
    for s in m.TOOL_CALL_ERRORS_TOTAL.collect():
        errors[s.labels["error_type"]] += s.value * (s.labels["mcp_server"] == sid)
    durations = sum(s.value for s in m.TOOL_CALL_DURATION_SECONDS.collect()[2] if s.labels["mcp_server"] == sid)
    return +calls, +errors, durations


def _once(error_type):
    return ({"success": 1}, {}, 1) if error_type is None else ({"error": 1}, {error_type: 1}, 0)


@pytest.mark.parametrize(("reply", "tool", "policy", "error_type"), CASES)
def test_a_call_through_the_command_bus_is_counted_once(reply, tool, policy, error_type):
    server, _, send = _setup(reply, policy)
    send(tool)
    assert _counts(server.mcp_server_id) == _once(error_type)


@pytest.mark.parametrize(("reply", "tool", "policy", "error_type"), CASES[:4])
def test_a_call_outside_the_bus_is_counted_from_its_event(reply, tool, policy, error_type):
    """`Hangar.invoke` calls the aggregate; the gc and health workers publish its events."""
    server, events, _ = _setup(reply, policy)
    with contextlib.suppress(Exception):
        server.invoke_tool(tool, {})
    events.publish_aggregate_events(MCP_SERVER, server.mcp_server_id, list(server.collect_events()))
    assert _counts(server.mcp_server_id) == _once(error_type)


def test_a_refusal_is_counted_when_its_publish_carries_another_calls_event():
    """A concurrent call's ToolInvocationFailed can ride this call's publish."""
    server, _, send = _setup(OSError("gone"))
    with contextlib.suppress(Exception):
        server.invoke_tool("add", {})  # records a Failed, left unpublished
    send("missing")
    assert _counts(server.mcp_server_id) == ({"error": 2}, {"OSError": 1, "ToolNotFoundError": 1}, 0)
