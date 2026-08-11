"""The MCP lifecycle is finished, not abandoned after `initialize` (#881).

The lifecycle has three steps. Hangar sent `initialize` then `tools/list` and
nothing in between, so every upstream was left permanently mid-handshake -- and
a server is entitled to defer work until the notification arrives. Against the
official reference server, which registers a tool in its `oninitialized`
handler, that cost a tool that was neither listed nor callable.

The fake here reproduces that rule rather than asserting on a call list alone:
a tool appears in `tools/list` only after the notification has been seen. A
test that only counted method names would keep passing if the notification were
sent in the wrong ORDER, which is the mistake most likely to be made here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from mcp_hangar.domain.model import McpServer

_METHOD_NOT_FOUND = -32601


class DeferringUpstream:
    """An upstream that registers a tool in its `oninitialized` handler."""

    def __init__(self, init_response: dict[str, Any] | None = None) -> None:
        self.methods: list[str] = []
        self.initialized = False
        self.process = None
        self._init_response = init_response or {"result": {"protocolVersion": "2026-07-28"}}

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        self.methods.append(method)
        if method == "initialize":
            return self._init_response
        if method == "tools/list":
            tools = [{"name": "add", "description": "", "inputSchema": {}}]
            if self.initialized:
                tools.append({"name": "simulate-research-query", "description": "", "inputSchema": {}})
            return {"result": {"tools": tools}}
        return {"result": {}}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.methods.append(method)
        if method == "notifications/initialized":
            self.initialized = True


def _server() -> McpServer:
    return McpServer(mcp_server_id="test", mode="subprocess", command=["test"])


def test_the_deferred_tool_is_discovered() -> None:
    client = DeferringUpstream()

    server = _server()
    server._perform_mcp_handshake(client)

    assert sorted(server.tools.list_names()) == ["add", "simulate-research-query"]


def test_the_notification_precedes_tools_list() -> None:
    """Order is the whole point -- after `tools/list` it buys nothing."""
    client = DeferringUpstream()

    _server()._perform_mcp_handshake(client)

    assert client.methods == ["initialize", "notifications/initialized", "tools/list"]


def test_a_stateless_upstream_is_not_sent_one() -> None:
    """No handshake happened, so there is no session to finish (SEP-2575)."""
    client = DeferringUpstream({"error": {"code": _METHOD_NOT_FOUND, "message": "Method not found"}})

    _server()._perform_mcp_handshake(client)

    assert "notifications/initialized" not in client.methods


def test_a_failing_notification_does_not_fail_the_start() -> None:
    """A spec MUST that no upstream answers must not become an availability regression."""
    client = DeferringUpstream()
    client.notify = MagicMock(side_effect=RuntimeError("upstream hung up"))  # type: ignore[method-assign]

    server = _server()
    server._perform_mcp_handshake(client)

    # Discovery still completed; only the deferred tool is missing.
    assert server.tools.list_names() == ["add"]


def test_a_client_without_notify_is_reported_not_crashed() -> None:
    """Third-party or stub transports predate this method; say so rather than raise."""

    class OldTransport(DeferringUpstream):
        notify = None  # type: ignore[assignment]

    client = OldTransport()

    server = _server()
    server._perform_mcp_handshake(client)

    assert server.tools.list_names() == ["add"]
