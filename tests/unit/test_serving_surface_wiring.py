"""What the shipped `serve` path wires onto its MCP server (#595, #596).

Both of these lived only in `MCPServerFactory`, which has no production call
site, so on `mcp-hangar serve --http` they simply did not happen:

- governance was advertised to nobody (`capabilities.experimental` empty), and
- `tool_access.mode: front_door` did nothing at all — the gateway kept serving
  the `hangar_*` meta-API, including lifecycle control, to callers the mode
  exists to fail closed on.

Asserted against `build_serving_mcp_server()`, the function `bootstrap()` itself
calls. Asserting the factory instead would reproduce the blind spot.
"""

from __future__ import annotations

import pytest

from mcp_hangar._sdk_compat import lowlevel_server
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.server.bootstrap import build_serving_mcp_server


@pytest.fixture(autouse=True)
def _clean_topology():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


def _tool_names(mcp) -> set[str]:
    return {tool.name for tool in mcp._tool_manager.list_tools()}


def _experimental(mcp) -> dict:
    return lowlevel_server(mcp).get_capabilities().model_dump(mode="json", exclude_none=True).get("experimental") or {}


class TestGovernanceIsAdvertised:
    def test_the_served_server_advertises_the_governance_extensions(self):
        """#595: the shipped path advertised an empty experimental map."""
        advertised = _experimental(build_serving_mcp_server())

        assert advertised, "capabilities.experimental is empty on the served server"
        assert any(key.startswith("io.mcp-hangar.") for key in advertised), advertised

    def test_advertising_goes_through_get_capabilities_so_discover_sees_it_too(self):
        """Injecting at the initialize-only seam would hide it from stateless clients.

        `server/discover` reads `get_capabilities`; it has no handshake. Wiring
        governance into `create_initialization_options` alone would advertise it
        to exactly the generation that cannot read it — the shape of #605.
        """
        from mcp_hangar.fastmcp_server.server_discover import server_discover_result

        mcp = build_serving_mcp_server()

        result = server_discover_result(None, mcp)

        assert result["capabilities"].get("experimental"), "governance is invisible on the stateless discovery surface"


class TestTopologyDecidesTheToolSurface:
    def test_egress_keeps_the_meta_api(self):
        """The default must be untouched: egress still serves hangar_*."""
        names = _tool_names(build_serving_mcp_server())

        assert any(name.startswith("hangar_") for name in names), names

    def test_the_flat_gate_reports_whether_it_engaged(self):
        """The shared gate is the single source of truth for both builders."""
        from mcp_hangar.fastmcp_server.flat_tool_projection import maybe_register_flat_tool_handlers

        egress_server = build_serving_mcp_server()
        assert maybe_register_flat_tool_handlers(egress_server) is False

        get_tool_access_resolver().set_topology_mode("front_door")
        front_door_server = build_serving_mcp_server()
        assert maybe_register_flat_tool_handlers(front_door_server) is True
