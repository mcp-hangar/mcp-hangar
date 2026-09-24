"""The routing half of #1366: a refreshed catalogue is projected before the nudge.

The served path is covered end to end in
``tests/integration/test_an_upstreams_list_changed_reaches_the_projection.py``.
These pin what that test cannot isolate: the order (the registry is rebuilt
before the front door tells clients to re-list), that a failed refresh or a
failed projection never swallows the nudge, and that one server's change leaves
every other server's projections alone.

Naming: neutral placeholders only (server_a, server_b, read_item, delete_item).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.tool_catalog import ToolCatalog, ToolSchema
from mcp_hangar.domain.services import subscription_relay, tool_catalogue_changes
from mcp_hangar.server.bootstrap.event_handlers import subscribe_tool_projection

LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}


def _schema(name: str) -> ToolSchema:
    return ToolSchema(name=name, description=f"Does {name}", input_schema={"type": "object", "properties": {}})


class _Server:
    def __init__(self, *names: str) -> None:
        self.tools = ToolCatalog()
        for name in names:
            self.tools.add(_schema(name))


class _Repo:
    def __init__(self, **servers: _Server) -> None:
        self._servers = servers

    def get(self, mcp_server_id: str) -> _Server | None:
        return self._servers.get(mcp_server_id)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_tool_projection_registry()
    tool_catalogue_changes.clear_listener()
    subscription_relay.clear_sink()
    yield
    reset_tool_projection_registry()
    tool_catalogue_changes.clear_listener()
    subscription_relay.clear_sink()


def _routing_server(refreshed: bool) -> MagicMock:
    server = MagicMock(spec=McpServer)
    server.mcp_server_id = "server_a"
    server._refresh_tools.return_value = refreshed
    server._route_upstream_message = McpServer._route_upstream_message.__get__(server)
    server._announce_catalogue = McpServer._announce_catalogue.__get__(server)
    return server


def _projected(mcp_server: str) -> list[str]:
    return sorted(p.tool for p in get_tool_projection_registry().all() if p.mcp_server == mcp_server)


def test_the_registry_is_rebuilt_before_clients_are_told_to_relist() -> None:
    order: list[str] = []
    tool_catalogue_changes.register_listener(lambda server_id: order.append(f"project:{server_id}"))
    subscription_relay.register_sink(lambda server_id, method, _params: order.append(f"nudge:{server_id}") is None)

    _routing_server(refreshed=True)._route_upstream_message(LIST_CHANGED)

    assert order == ["project:server_a", "nudge:server_a"]


def test_a_failed_refresh_announces_nothing_and_still_nudges() -> None:
    announced: list[str] = []
    nudged: list[str] = []
    tool_catalogue_changes.register_listener(announced.append)
    subscription_relay.register_sink(lambda server_id, _method, _params: nudged.append(server_id) is None)

    _routing_server(refreshed=False)._route_upstream_message(LIST_CHANGED)

    assert announced == []
    assert nudged == ["server_a"]


def test_a_failed_projection_does_not_swallow_the_nudge() -> None:
    nudged: list[str] = []

    def broken(_server_id: str) -> None:
        raise RuntimeError("projection broke")

    tool_catalogue_changes.register_listener(broken)
    subscription_relay.register_sink(lambda server_id, _method, _params: nudged.append(server_id) is None)

    _routing_server(refreshed=True)._route_upstream_message(LIST_CHANGED)

    assert nudged == ["server_a"]


def test_an_announcement_rebuilds_that_server_and_leaves_the_others_alone() -> None:
    server_a = _Server("read_item", "write_item")
    repo = _Repo(server_a=server_a, server_b=_Server("read_item", "write_item"))
    subscribe_tool_projection(MagicMock(), repo)  # type: ignore[arg-type]
    registry = get_tool_projection_registry()
    registry.build_from_tools("server_a", server_a.tools.list_tools())
    registry.build_from_tools("server_b", repo.get("server_b").tools.list_tools())  # type: ignore[union-attr]
    registry.withdraw("server_b", "write_item", tenant_id="tenant:b")
    server_b_before = registry.resolve("server_b", "write_item", "tenant:b")

    server_a.tools.update_from_list([{"name": "read_item"}, {"name": "delete_item"}])
    assert tool_catalogue_changes.announce("server_a") is True

    assert _projected("server_a") == ["delete_item", "read_item"]
    assert _projected("server_b") == ["read_item", "write_item"]
    assert registry.resolve("server_b", "write_item", "tenant:b") == server_b_before


def test_subscribing_wires_both_triggers() -> None:
    bus = MagicMock()

    handler = subscribe_tool_projection(bus, _Repo())  # type: ignore[arg-type]

    bus.subscribe.assert_called_once()
    assert tool_catalogue_changes.announce("unknown") is True  # taken, and a no-op for an unknown server
    assert bus.subscribe.call_args.args[1] == handler.handle
