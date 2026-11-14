"""A front door lists and routes only the tools ``tool_projection.flat`` names, on the served app (#1372).

The real app ``serve --http`` builds, with API-key auth, over one upstream that
serves three tools. The front door names two of them. A tool it does not name
is not listed and its call answers ``-32601``, the answer a name that does not
exist gets, so the list is not an oracle for what the upstream serves (#905).
Naming a tool does not override a tenant's policy. A change to the list is
pushed to a listening client as ``notifications/tools/list_changed``.

Over real streamable HTTP: ``tests/live/test_t0_front_door_flat_is_opt_in.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.fastmcp_server import tool_list_changed
from mcp_hangar.fastmcp_server.flat_tool_projection import generate_projection

from ._front_door_harness import SERVER, TENANT_A, TENANT_B, front_door, jsonrpc

METHOD_NOT_FOUND = -32601
TOOLS = ("read_item", "write_item", "delete_item")


def _named(*names: str) -> None:
    get_tool_projection_registry().set_flat(SERVER, frozenset(names))


def test_only_the_named_tools_are_listed() -> None:
    with front_door(TOOLS) as door:
        _named("read_item", "write_item")

        assert door.names(TENANT_A) == ["read_item", "write_item"]


def test_a_named_tool_is_served_and_an_unnamed_one_is_refused_like_a_missing_name() -> None:
    with front_door(TOOLS) as door:
        _named("read_item", "write_item")

        door.result(TENANT_A, "read_item", {"x": "1"})
        unnamed, missing = door.error(TENANT_A, "delete_item"), door.error(TENANT_A, "no_such_tool")

        assert unnamed["code"] == missing["code"] == METHOD_NOT_FOUND
        assert unnamed["message"].replace("delete_item", "X") == missing["message"].replace("no_such_tool", "X")
        assert "delete_item" not in door.upstream.called


def test_naming_a_tool_does_not_override_a_tenants_policy() -> None:
    with front_door(TOOLS, {TENANT_B: ("write_item",)}) as door:
        _named("read_item", "write_item")

        assert door.names(TENANT_B) == ["write_item"]
        assert door.error(TENANT_B, "read_item")["code"] == METHOD_NOT_FOUND


def test_hangar_call_is_not_served_on_the_front_door() -> None:
    # The only other invoke path, and the front door does not project it (`INVOKE_PATH_TOOLS`).
    with front_door(TOOLS) as door:
        _named("read_item")
        calls = {"calls": [{"mcp_server": SERVER, "tool": "delete_item", "arguments": {}}]}
        payload = jsonrpc(door.call(TENANT_A, "hangar_call", calls))

        assert payload["error"]["code"] == METHOD_NOT_FOUND
        assert "delete_item" not in door.upstream.called


def test_a_new_list_is_pushed_to_a_listening_client() -> None:
    with front_door(TOOLS) as door:
        _named("read_item")
        channel = SimpleNamespace(notified=0)

        async def _notify() -> None:
            channel.notified += 1

        session = Mock(send_tool_list_changed=_notify)
        ctx = SimpleNamespace(session=session, protocol_version="2025-11-25")

        async def _scenario() -> None:
            tool_list_changed.serve_push_channel("stdio")
            tool_list_changed.track_listing(ctx, generate_projection(TENANT_A))
            await asyncio.sleep(0)
            _named("read_item", "write_item")
            for _ in range(100):
                if channel.notified:
                    return
                await asyncio.sleep(0.05)

        try:
            asyncio.run(_scenario())
        finally:
            tool_list_changed.reset()

        assert door.names(TENANT_A) == ["read_item", "write_item"]
        assert channel.notified == 1
