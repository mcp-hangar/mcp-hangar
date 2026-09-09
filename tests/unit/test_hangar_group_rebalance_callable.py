"""hangar_group_rebalance must accept the argument the SDK actually sends (#1209).

The tool's own parameter is ``group``, but the validator wired via
``mcp_tool_wrapper(validate=...)`` used to be ``validate_mcp_server_id_input``,
whose parameter is ``mcp_server``. MCP delivers tool arguments by name, so no
call could ever satisfy both -- the tool was uncallable since it was
introduced. This exercises the real production wiring through FastMCP's own
``call_tool``, which is what delivers arguments by name in the first place.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

from mcp_hangar._sdk_compat import FastMCP
from mcp_hangar.server.tools.groups import register_group_tools


def _fake_context() -> Mock:
    member = Mock(id="weather-open-meteo", in_rotation=True)
    group = Mock(healthy_count=1, total_count=1, members=[member])
    group.state.value = "ready"

    ctx = Mock()
    ctx.group_exists.return_value = True
    ctx.get_group.return_value = group
    ctx.rate_limiter.consume.return_value = Mock(allowed=True)
    return ctx


def test_hangar_group_rebalance_accepts_its_own_schema():
    mcp = FastMCP("test")
    register_group_tools(mcp)

    ctx = _fake_context()
    with (
        patch("mcp_hangar.server.tools.groups.get_context", return_value=ctx),
        patch("mcp_hangar.server.validation.get_context", return_value=ctx),
    ):
        result = asyncio.run(mcp.call_tool("hangar_group_rebalance", {"group": "weather"}))

    assert result.is_error is False, result.content
    ctx.get_group.assert_called_once_with("weather")
