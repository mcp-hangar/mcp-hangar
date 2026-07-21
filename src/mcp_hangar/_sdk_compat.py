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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Static typing follows the SDK v1 FastMCP surface until the pin flips to v2,
    # so mypy keeps checking the server surface (the runtime resolution below
    # would otherwise degrade FastMCP to Any under ignore_missing_imports).
    from mcp.server.fastmcp import Context, FastMCP
else:
    try:  # SDK v2: FastMCP -> MCPServer; Context moved to mcp.server.mcpserver.
        from mcp.server.mcpserver import Context, MCPServer as FastMCP
    except ImportError:  # SDK v1
        from mcp.server.fastmcp import Context, FastMCP

def current_request_context():
    """Best-effort access to the ambient MCP request context, or ``None``.

    SDK v1 exposes the in-flight request on the module-level ``request_ctx``
    ContextVar (``mcp.server.lowlevel.server``). SDK v2 removed that ambient var:
    the request context is passed explicitly to a tool via its ``Context``
    (``Context.request_context``), so there is no ambient equivalent. Callers
    that only have this fallback therefore get ``None`` on v2 and must degrade
    gracefully (they are fault-barriered to server-level policy). Threading the
    v2 ``Context`` through to those sites is a follow-up under #547.
    """
    try:  # SDK v1: ambient request ContextVar.
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:  # SDK v2: no ambient request_ctx (Context is passed explicitly).
        return None
    return request_ctx.get(None)


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
    "current_request_context",
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
