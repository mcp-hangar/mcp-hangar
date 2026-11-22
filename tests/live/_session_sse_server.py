"""A session-based Python MCP SDK server over Streamable HTTP, for #2327.

    python _session_sse_server.py <port>

Stateful (``stateless_http=False``, ``json_response=False``) with an in-memory
``EventStore`` so every SSE frame is emitted by ``sse-starlette`` with CRLF
line endings on both:

- the standing GET stream (``announce_change`` registers ``late_tool`` and
  pushes ``notifications/tools/list_changed`` there), and
- a resumed GET stream after SEP-1699 early POST close (``resumed_answer``
  calls ``ctx.close_sse_stream()`` before returning its answer).
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from typing import Any

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.streamable_http import EventCallback, EventId, EventMessage, EventStore, StreamId
from mcp.types import JSONRPCMessage
from starlette.responses import JSONResponse


class _MemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._counter = 0
        self._streams: dict[StreamId, list[tuple[EventId, JSONRPCMessage | None]]] = defaultdict(list)
        self._event_stream: dict[EventId, StreamId] = {}

    async def store_event(self, stream_id: StreamId, message: JSONRPCMessage | None) -> EventId:
        self._counter += 1
        event_id = f"ev-{self._counter}"
        self._streams[stream_id].append((event_id, message))
        self._event_stream[event_id] = stream_id
        return event_id

    async def replay_events_after(self, last_event_id: EventId, send_callback: EventCallback) -> StreamId | None:
        stream_id = self._event_stream.get(last_event_id)
        if stream_id is None:
            return None
        seen = False
        for event_id, message in list(self._streams[stream_id]):
            if seen:
                if message is not None:
                    await send_callback(EventMessage(message=message, event_id=event_id))
            elif event_id == last_event_id:
                seen = True
        return stream_id


mcp = MCPServer("session-sse-provider")
_added = False


@mcp.tool(name="announce_change")
async def announce_change(ctx: Context) -> str:
    """Register ``late_tool`` and notify the client on the standing GET stream."""
    global _added
    if not _added:
        _added = True

        @mcp.tool(name="late_tool")
        async def late_tool() -> str:
            """Added after initial discovery."""
            return "late-ok"

    await ctx.session.send_tool_list_changed()
    return "announced"


@mcp.tool(name="resumed_answer")
async def resumed_answer(ctx: Context) -> str:
    """Close the POST SSE stream early (SEP-1699) and deliver the answer on resume."""
    if ctx.request_context is None or ctx.request_context.close_sse_stream is None:
        raise RuntimeError("close_sse_stream was not enabled on this request")
    await ctx.close_sse_stream()
    await asyncio.sleep(0.15)
    return "resumed-ok"


class _LegacyOnlyMiddleware:
    """Refuse ``server/discover`` so Hangar negotiates a legacy 2025-11-25 session."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            raw_headers = list(scope.get("headers", []))
            headers = {k.lower(): v for k, v in raw_headers}
            if headers.get(b"mcp-method") == b"server/discover":
                response = JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32601, "message": "Method not found"}}
                )
                await response(scope, receive, send)
                return
            # The Python SDK defaults an absent MCP-Protocol-Version header to
            # 2025-03-26, which disables SEP-1699 priming events; stamp the
            # negotiated 2025-11-25 session version so close_sse_stream is active.
            if b"mcp-session-id" in headers and b"mcp-protocol-version" not in headers:
                raw_headers.append((b"mcp-protocol-version", b"2025-11-25"))
                scope = {**scope, "headers": raw_headers}
        await self.app(scope, receive, send)


if __name__ == "__main__":
    port = int(sys.argv[1])
    app = _LegacyOnlyMiddleware(
        mcp.streamable_http_app(
            host="127.0.0.1",
            stateless_http=False,
            json_response=False,
            event_store=_MemoryEventStore(),
            retry_interval=100,
        )
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
