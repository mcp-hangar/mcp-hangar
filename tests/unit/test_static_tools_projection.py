"""Tests documenting the static `tools:` projection contract (#415).

A statically-configured `tools:` list is a PRE-START VISIBILITY PROJECTION
only. On start, the provider's dynamic `tools/list` is authoritative and
REPLACES the projection. A statically-listed tool the provider does not
return becomes uncallable (raises ToolNotFoundError) -- and start logs a
WARNING naming the unconfirmed tool. These tests pin that behavior.
"""

from typing import Any

from structlog.testing import capture_logs

from mcp_hangar.domain.exceptions import ToolNotFoundError
from mcp_hangar.domain.model.provider import McpServer
from mcp_hangar.domain.value_objects import ProviderMode, ProviderState

STATIC_TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {"type": "object"},
    }
]


class _FakeClient:
    """Minimal client stub returning a canned initialize + tools/list."""

    def __init__(self, tools_list: list[dict]) -> None:
        self._tools_list = tools_list

    def is_alive(self) -> bool:
        return True

    def call(self, method: str, params: dict, timeout: float | None = None) -> dict[str, Any]:
        if method == "initialize":
            return {"result": {"capabilities": {}}}
        if method == "tools/list":
            return {"result": {"tools": self._tools_list}}
        if method == "tools/call":
            return {"result": {"content": []}}
        raise AssertionError(f"unexpected method: {method}")


def _make_provider() -> McpServer:
    return McpServer(
        mcp_server_id="static-test",
        mode=ProviderMode.SUBPROCESS,
        command=["echo"],
        tools=STATIC_TOOLS,
    )


def test_static_tool_visible_before_start() -> None:
    """The static list projects the tool for pre-start visibility."""
    provider = _make_provider()
    assert provider._tools.has("add")
    assert provider._tools_predefined_names == frozenset({"add"})


def test_handshake_warns_when_static_tool_unconfirmed() -> None:
    """Provider returning no tools -> WARNING names the missing static tool
    and the catalog no longer contains it (dynamic list is authoritative)."""
    provider = _make_provider()
    client = _FakeClient(tools_list=[])  # provider confirms nothing

    with capture_logs() as logs:
        provider._perform_mcp_handshake(client)

    assert not provider._tools.has("add")  # projection replaced
    warnings = [e for e in logs if e.get("log_level") == "warning"]
    assert any("static_tools_unconfirmed" in e["event"] and "add" in e["event"] for e in warnings)


def test_handshake_no_warning_when_static_tool_confirmed() -> None:
    """When the provider returns the statically-listed tool, no warning."""
    provider = _make_provider()
    client = _FakeClient(tools_list=STATIC_TOOLS)

    with capture_logs() as logs:
        provider._perform_mcp_handshake(client)

    assert provider._tools.has("add")
    assert not any("static_tools_unconfirmed" in e.get("event", "") for e in logs)


def test_invoke_of_unconfirmed_static_tool_raises_not_found() -> None:
    """Current behavior: a static tool the provider never confirms is
    uncallable -- invoke raises ToolNotFoundError."""
    provider = _make_provider()
    client = _FakeClient(tools_list=[])
    # Start handshake replaces the static projection with the provider's
    # (empty) dynamic list -- exactly what happens at real start.
    provider._perform_mcp_handshake(client)
    provider._client = client
    provider._state = ProviderState.READY

    try:
        provider.invoke_tool("add", {})
        raise AssertionError("expected ToolNotFoundError")
    except ToolNotFoundError:
        pass

    assert not provider._tools.has("add")
