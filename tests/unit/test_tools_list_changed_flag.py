"""The capability flags across mode x protocol era x transport (#1366, part A).

On the handshake era ``tools.listChanged`` is true exactly where the front door
pushes the notification: front_door, over a transport it can push on (stdio).
front_door HTTP has no back-channel yet (#877), and egress publishes nothing.
The other three handshake-era flags stay false, and the 2026-07-28 flags still
follow ``subscriptions/listen`` alone, whatever the transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar._sdk_compat import lowlevel_server
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.fastmcp_server import tool_list_changed

MODERN = "2026-07-28"


@pytest.fixture(autouse=True)
def _clean() -> Any:
    reset_tool_access_resolver()
    tool_list_changed.reset()
    yield
    reset_tool_access_resolver()
    tool_list_changed.reset()


def _flags(capabilities: Any) -> dict[str, bool]:
    tools, resources, prompts = capabilities.tools, capabilities.resources, capabilities.prompts
    return {
        "tools.listChanged": bool(tools and tools.list_changed),
        "resources.subscribe": bool(resources and resources.subscribe),
        "resources.listChanged": bool(resources and resources.list_changed),
        "prompts.listChanged": bool(prompts and prompts.list_changed),
    }


def _served(mode: str, transport: str) -> Any:
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    get_tool_access_resolver().set_topology_mode(mode)
    low = lowlevel_server(build_serving_mcp_server())
    if transport == "stdio":
        # What `ServerLifecycle.run_stdio` records before it serves.
        tool_list_changed.serve_push_channel("stdio")
    return low


@pytest.mark.parametrize(
    ("mode", "transport", "tools_list_changed"),
    [
        ("front_door", "stdio", True),
        ("front_door", "http", False),  # until the sessionless GET stream lands
        ("egress", "stdio", False),
        ("egress", "http", False),
    ],
)
def test_the_handshake_era_advertises_list_changed_only_where_it_is_pushed(
    mode: str, transport: str, tools_list_changed: bool
) -> None:
    low = _served(mode, transport)

    # The payload the SDK answers `initialize` with, as `run_stdio_async` builds it.
    flags = _flags(low.create_initialization_options().capabilities)

    assert flags == {
        "tools.listChanged": tools_list_changed,
        "resources.subscribe": False,
        "resources.listChanged": False,
        "prompts.listChanged": False,
    }


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("mode", ["front_door", "egress"])
def test_the_2026_flags_still_follow_subscriptions_listen(mode: str, transport: str) -> None:
    low = _served(mode, transport)
    listen = "subscriptions/listen" in low._request_handlers

    flags = _flags(low.get_capabilities(protocol_version=MODERN))

    assert listen is (mode == "front_door")
    # tools/list is always served; the others only count where advertised at all.
    assert flags["tools.listChanged"] is listen
    assert all(value in (listen, False) for value in flags.values())
