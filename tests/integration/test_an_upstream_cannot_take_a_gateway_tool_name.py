"""An upstream tool named like one of the gateway's own tools is not projected (#2031).

On the front door every upstream tool name is a flat name, and a call looks the
name up among the upstream tools before the gateway's own. An upstream serving
`hangar_status` therefore got the name listed twice and answered calls of it,
so a client asking the gateway for its status read whatever the upstream said.

Over the real streamable-HTTP transport, through the composition ``serve
--http`` serves (`_front_door_harness`).
"""

from __future__ import annotations

import asyncio

from mcp_hangar.server.bootstrap import build_serving_mcp_server
from tests.integration._front_door_harness import METHOD_NOT_FOUND, TENANT_A, front_door

TOOL = "read_item"
TAKEN = ("hangar_status", "hangar_whoami", "hangar_call")


def test_the_upstreams_copy_is_neither_listed_nor_called() -> None:
    with front_door((TOOL, *TAKEN)) as door:
        names = door.names(TENANT_A)

        assert TOOL in names
        assert names.count("hangar_status") <= 1
        for name in TAKEN:
            door.call(TENANT_A, name)
        assert not set(TAKEN) & set(door.upstream.called), door.upstream.called


def test_the_caller_gets_what_it_would_without_that_upstream() -> None:
    with front_door((TOOL,)) as plain:
        expected = {name: plain.error(TENANT_A, name)["code"] for name in TAKEN}
        listed = plain.names(TENANT_A)
    with front_door((TOOL, *TAKEN)) as door:
        assert door.names(TENANT_A) == listed
        assert {name: door.error(TENANT_A, name)["code"] for name in TAKEN} == expected
        assert set(expected.values()) == {METHOD_NOT_FOUND}


def test_the_upstreams_other_tools_are_unaffected() -> None:
    with front_door((TOOL, "hangar_status")) as door:
        assert door.result(TENANT_A, TOOL)
        assert TOOL in door.upstream.called


def test_every_tool_the_gateway_registers_is_reserved() -> None:
    # A tool added to the gateway later is reserved without anyone remembering to.
    from mcp_hangar.fastmcp_server.flat_tool_projection import _reserved_names

    registered = {tool.name for tool in asyncio.run(build_serving_mcp_server().list_tools())}

    assert registered == _reserved_names()
