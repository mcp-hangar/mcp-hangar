"""A projection change reaches the handshake-era channels whose tenant it changed (#1366).

Everything is real except the channels: the front door built the way
``serve --http`` builds it (which registers the publisher), a real remote
``McpServer`` started through the command bus, the projection registry and its
change listeners, and ``generate_projection`` doing the comparison. A channel is
what a stdio session is to the publisher, a ``send_tool_list_changed`` on a
connection, so each tenant gets a stand-in one, recorded through the same
``track_listing`` the ``tools/list`` handler calls. The stdio transport itself
is covered end to end in ``test_stdio_tools_list_changed.py``.

Registry mutations are made from worker threads, as a warm-up, a GET stream
reader and a call path make them, so the handoff to the loop is exercised.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.fastmcp_server import catalogue_warmup, tool_list_changed
from mcp_hangar.fastmcp_server.flat_tool_projection import generate_projection

from ._front_door_harness import LEGACY, SERVER, TENANT_A, TENANT_B, FrontDoor, front_door, jsonrpc

HANDSHAKE = "2025-11-25"
#: Past the coalescing window, with room for a loaded CI runner.
SETTLE_S = tool_list_changed.COALESCE_WINDOW_S * 4


class _Channel:
    """A handshake-era connection as the publisher sees it: something to notify."""

    def __init__(self) -> None:
        self.notified = 0
        self._connection = object()

    async def send_tool_list_changed(self) -> None:
        self.notified += 1


@pytest.fixture
def gateway() -> Iterator[FrontDoor]:
    tool_list_changed.reset()
    with front_door(("read_item", "write_item")) as door:
        tool_list_changed.serve_push_channel("stdio")
        try:
            yield door
        finally:
            tool_list_changed.reset()


def _listen(tenant: str) -> _Channel:
    """What a `tools/list` on a handshake-era connection of *tenant* records."""
    channel = _Channel()
    ctx = SimpleNamespace(session=channel, protocol_version=HANDSHAKE)
    tool_list_changed.track_listing(ctx, generate_projection(tenant))
    return channel


def _tool(name: str) -> ToolSchema:
    return ToolSchema(name=name, description="", input_schema={"type": "object"})


async def _settle() -> None:
    await asyncio.sleep(SETTLE_S)


async def test_only_the_tenant_whose_projection_changed_is_told(gateway: FrontDoor) -> None:
    a, b = _listen(TENANT_A), _listen(TENANT_B)
    await _settle()  # the first-listing check, which finds nothing changed
    assert (a.notified, b.notified) == (0, 0)

    await asyncio.to_thread(get_tool_projection_registry().withdraw, SERVER, "write_item", TENANT_A)
    await _settle()

    assert a.notified == 1
    assert b.notified == 0, "tenant B was told about a change to tenant A's projection"


async def test_a_change_that_leaves_the_projection_alone_notifies_nobody(gateway: FrontDoor) -> None:
    from mcp_hangar.domain.services import tool_catalogue_changes

    a = _listen(TENANT_A)
    await _settle()

    # An upstream's `list_changed` with nothing new: the registry is rebuilt
    # from the same catalogue, the listeners fire, and the comparison finds
    # nothing to tell.
    await asyncio.to_thread(tool_catalogue_changes.announce, SERVER)
    await _settle()

    assert a.notified == 0


async def test_a_hot_loaded_upstream_notifies_the_channels_it_reaches(gateway: FrontDoor) -> None:
    a, b = _listen(TENANT_A), _listen(TENANT_B)
    await _settle()

    # A server added while serving lands in the registry the way a start does.
    await asyncio.to_thread(get_tool_projection_registry().build_from_tools, "catalog", [_tool("find_item")])
    await _settle()

    assert "find_item" in generate_projection(TENANT_A).tools
    assert (a.notified, b.notified) == (1, 1)


async def test_many_upstreams_landing_at_once_cost_each_channel_one_notification(gateway: FrontDoor) -> None:
    channels = [_listen(TENANT_A) for _ in range(3)] + [_listen(TENANT_B)]
    await _settle()
    registry = get_tool_projection_registry()

    def storm() -> None:
        for n in range(40):
            registry.build_from_tools(f"shelf-{n}", [_tool(f"shelf_item_{n}")])

    await asyncio.to_thread(storm)
    await _settle()

    # Every channel changed, and each was told once for the lot.
    assert [channel.notified for channel in channels] == [1, 1, 1, 1]


async def test_the_end_of_the_warm_up_flushes_without_waiting_out_the_window(
    gateway: FrontDoor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool_list_changed, "COALESCE_WINDOW_S", 30.0)
    a = _listen(TENANT_A)
    await asyncio.to_thread(get_tool_projection_registry().build_from_tools, "catalog", [_tool("find_item")])

    catalogue_warmup.warmup_finished()
    tool_list_changed.flush_now()  # what the warm-up's `finally` does after it
    await asyncio.sleep(0.2)

    assert a.notified == 1


async def test_a_call_that_finds_a_new_tool_is_announced(gateway: FrontDoor) -> None:
    from mcp_hangar.server.context import get_context

    a = _listen(TENANT_A)
    await _settle()
    gateway.upstream.tools = ("read_item", "write_item", "late_item")
    server = get_context().runtime.repository.get(SERVER)

    # The lazy refresh inside `invoke_tool` is what finds `late_item`.
    await asyncio.to_thread(server.invoke_tool, "late_item", {})
    await _settle()

    assert "late_item" in generate_projection(TENANT_A).tools
    assert a.notified == 1


def test_front_door_http_advertises_what_its_get_stream_pushes() -> None:
    tool_list_changed.reset()
    with front_door(("read_item",)) as door:
        try:
            params: dict[str, Any] = {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "0"},
            }
            answer = jsonrpc(door.post(TENANT_A, "initialize", params, era=LEGACY))
        finally:
            tool_list_changed.reset()

    assert answer["result"]["capabilities"]["tools"].get("listChanged") is True
