"""SDK-version-agnostic re-exports for the mcp SDK v1 -> v2 migration (#547).

In SDK v2 the protocol **types** moved out of ``mcp.types`` into the split
``mcp-types`` distribution (import root ``mcp_types``), and the exception
``McpError`` was renamed ``MCPError``. Importing those names from this module
(instead of ``mcp.types`` / ``mcp.shared.exceptions`` directly) makes the same
source work on **both** SDK generations:

- SDK v1 (``mcp>=1.28,<2``): resolves from ``mcp.types`` / ``McpError``.
- SDK v2 (``mcp>=2.0.0b``): resolves from ``mcp_types`` / ``MCPError``.

Phase 1 of the migration routes the type + exception surface through here, so
flipping the pin to SDK v2 does not have to touch every call site. The FastMCP
server surface (a much larger change) is handled in a later phase.
"""

from __future__ import annotations

try:  # SDK v2: protocol types live in the split ``mcp_types`` package.
    import mcp_types as _t
except ImportError:  # SDK v1: protocol types live under ``mcp.types``.
    from mcp import types as _t

try:  # SDK v2 renamed McpError -> MCPError (mcp.shared.exceptions still exists).
    from mcp.shared.exceptions import MCPError as McpError  # type: ignore[attr-defined]
except ImportError:  # SDK v1
    from mcp.shared.exceptions import McpError

try:  # SDK v2: FastMCP -> MCPServer; Context moved to mcp.server.mcpserver.
    from mcp.server.mcpserver import (
        Context,
        MCPServer as FastMCP,
    )
except ImportError:  # SDK v1
    from mcp.server.fastmcp import Context, FastMCP

# Protocol version constants.
DEFAULT_NEGOTIATED_VERSION = _t.DEFAULT_NEGOTIATED_VERSION
LATEST_PROTOCOL_VERSION = _t.LATEST_PROTOCOL_VERSION

# Error codes.
INVALID_PARAMS = _t.INVALID_PARAMS
METHOD_NOT_FOUND = _t.METHOD_NOT_FOUND

# Result / content / tool / task types.
CallToolResult = _t.CallToolResult
TextContent = _t.TextContent
ErrorData = _t.ErrorData
Result = _t.Result
ListToolsResult = _t.ListToolsResult
Tool = _t.Tool
Task = _t.Task
TaskMetadata = _t.TaskMetadata
TaskStatus = _t.TaskStatus

__all__ = [
    "FastMCP",
    "Context",
    "McpError",
    "DEFAULT_NEGOTIATED_VERSION",
    "LATEST_PROTOCOL_VERSION",
    "INVALID_PARAMS",
    "METHOD_NOT_FOUND",
    "CallToolResult",
    "TextContent",
    "ErrorData",
    "Result",
    "ListToolsResult",
    "Tool",
    "Task",
    "TaskMetadata",
    "TaskStatus",
]
