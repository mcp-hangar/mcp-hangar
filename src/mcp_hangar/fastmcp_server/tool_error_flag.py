"""Mark a `hangar_*` tool's error payload as an error on the wire (#1669).

A tool body that fails answers with `ToolErrorPayload.to_dict()`, a plain dict.
The SDK serializes a dict as structured content with `isError: false`, so a
generic MCP client, which decides success from that flag, treated a refused or
failed call as a success.

The flag is set here, at the one place every tool call leaves through, and not
in the tool wrapper. The wrapper's callers in process (the batch executor,
tests, anything that reads `result["error"]`) keep getting the dict they always
got; only what a client receives changes.

Two conditions must both hold, so a success is never relabelled:

* the tool is one of Hangar's own (`hangar_*`). A proxied backend tool is
  never touched: whatever it returned is its own answer, error key or not.
* the answer is exactly the three keys `ToolErrorPayload` carries, with the
  types it gives them. A tool with a declared output schema answers in
  `structuredContent`; one without (every `hangar_*` tool today) answers only
  as one JSON text block, so both are read.
"""

from __future__ import annotations

import json
from typing import Any

from mcp_hangar._sdk_compat import FastMCP, lowlevel_server

NATIVE_TOOL_PREFIX = "hangar_"
_ERROR_KEYS = frozenset({"error", "error_type", "details"})
#: A payload is a few hundred bytes. A text block past this is a real result.
_MAX_PAYLOAD_TEXT = 64 * 1024


def is_error_payload(structured: Any) -> bool:
    """True for the dict `ToolErrorPayload.to_dict()` builds, and for nothing else."""
    return (
        isinstance(structured, dict)
        and structured.keys() == _ERROR_KEYS
        and isinstance(structured["error"], str)
        and isinstance(structured["error_type"], str)
        and isinstance(structured["details"], dict)
    )


def _payload_of(result: dict[str, Any]) -> Any:
    """What the tool answered with: its structured content, else its one JSON text block."""
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not (isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict)):
        return None
    text = content[0].get("text")
    if not (isinstance(text, str) and len(text) <= _MAX_PAYLOAD_TEXT and '"error_type"' in text):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


async def flag_tool_errors(ctx: Any, call_next: Any) -> Any:
    """Server middleware: set `isError` on a `hangar_*` call that answered with an error payload."""
    result = await call_next(ctx)
    if getattr(ctx, "method", None) != "tools/call":
        return result
    name = (getattr(ctx, "params", None) or {}).get("name")
    if not (isinstance(name, str) and name.startswith(NATIVE_TOOL_PREFIX)):
        return result
    # The chain carries the wire dict (camelCase), which is what a client is sent.
    if not isinstance(result, dict) or result.get("isError") is not False:
        return result
    if not is_error_payload(_payload_of(result)):
        return result
    return {**result, "isError": True}


def install_tool_error_flag(mcp: FastMCP) -> None:
    """Add :func:`flag_tool_errors` to the server's middleware chain, once."""
    middleware = getattr(lowlevel_server(mcp), "middleware", None)
    if isinstance(middleware, list) and flag_tool_errors not in middleware:
        middleware.append(flag_tool_errors)
