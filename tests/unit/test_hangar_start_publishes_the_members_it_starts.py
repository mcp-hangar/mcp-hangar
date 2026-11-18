"""`hangar_start(<group>)` publishes what the group's members record (#2265).

A group starts and stops its members itself, not through the command bus, so
each member's `McpServerStarted` and the group's own events stayed recorded
until a later drain. In egress the members were not projected until then, and a
withholding `header_exposure` block refused their tools as `schema_unknown`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mcp_hangar.application.commands.crud_commands import DeleteGroupCommand
from mcp_hangar.application.commands.crud_handlers import DeleteGroupHandler
from mcp_hangar.domain.events import McpServerStarted, McpServerStopped
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import GroupStateChanged, McpServerGroup
from mcp_hangar.server.tools import hangar
from mcp_hangar.stream_ids import MCP_SERVER

_MEMBERS = ("alpha", "beta")


class _Upstream:
    modern_envelope = False

    def is_alive(self) -> bool:
        return True

    def call(self, method, params, timeout=None):
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-11-25"}}
        return {"result": {"tools": [{"name": "lookup"}]}}

    def notify(self, method, params=None) -> None:
        pass

    def close(self) -> None:
        pass


def _group() -> McpServerGroup:
    group = McpServerGroup(group_id="pool", auto_start=False)
    for name in _MEMBERS:
        server = McpServer(mcp_server_id=name, mode="subprocess", command=["unused"], metrics_publisher=Mock())
        server._create_client = lambda: _Upstream()  # type: ignore[method-assign]
        server.collect_events()
        group.add_member(server)
    group.collect_events()
    return group


@pytest.fixture()
def fleet(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    group = _group()
    bus = Mock()
    ctx = SimpleNamespace(
        group_exists=lambda name: name == "pool",
        get_group=lambda _name: group,
        event_bus=bus,
        mcp_server_exists=lambda _name: False,
    )
    monkeypatch.setattr(hangar, "get_context", lambda: ctx)
    monkeypatch.setattr(hangar, "charge_tool", lambda _tool: None)
    monkeypatch.setattr(hangar, "_caller_tenant_id", lambda: None)
    tools: dict = {}
    stub = SimpleNamespace(tool=lambda name, **_k: lambda fn: tools.setdefault(name, fn))
    monkeypatch.setattr(hangar, "mcp_tool_wrapper", lambda **_k: lambda fn: fn)
    hangar.register_hangar_tools(stub)  # type: ignore[arg-type]
    return SimpleNamespace(group=group, bus=bus, tools=tools)


def _member_events(bus: Mock) -> dict[str, list]:
    return {
        call.args[1]: list(call.args[2])
        for call in bus.publish_aggregate_events.call_args_list
        if call.args[0] == MCP_SERVER
    }


class TestHangarStartOnAGroup:
    def test_each_started_member_is_published_to_its_own_stream(self, fleet: SimpleNamespace):
        result = fleet.tools["hangar_start"]("pool")

        assert result["members_started"] == 2
        published = _member_events(fleet.bus)
        assert set(published) == set(_MEMBERS)
        for events in published.values():
            assert any(isinstance(event, McpServerStarted) for event in events)
        for member in fleet.group.members:
            assert member.mcp_server.has_uncommitted_events() is False

    def test_the_groups_own_events_are_published_too(self, fleet: SimpleNamespace):
        fleet.tools["hangar_start"]("pool")

        local = [call.args[0] for call in fleet.bus.publish_local.call_args_list]
        assert any(isinstance(event, GroupStateChanged) for event in local)
        assert fleet.group.has_uncommitted_events() is False


class TestHangarStopOnAGroup:
    def test_each_stopped_member_is_published(self, fleet: SimpleNamespace):
        fleet.tools["hangar_start"]("pool")
        fleet.bus.reset_mock()

        fleet.tools["hangar_stop"]("pool")

        published = _member_events(fleet.bus)
        assert set(published) == set(_MEMBERS)
        for events in published.values():
            assert any(isinstance(event, McpServerStopped) for event in events)


class TestDeleteGroup:
    def test_the_members_stops_are_published(self):
        group = _group()
        group.start_all()
        group.collect_events()
        for member in group.members:
            member.mcp_server.collect_events()
        bus = Mock()

        DeleteGroupHandler({"pool": group}, bus).handle(DeleteGroupCommand(group_id="pool"))

        published = _member_events(bus)
        assert set(published) == set(_MEMBERS)
        for events in published.values():
            assert any(isinstance(event, McpServerStopped) for event in events)
