"""Unit tests for ToolProjectionPopulationHandler (issue #248).

Verifies that discovered backend tools populate the ToolProjectionRegistry on
McpServerStarted, that re-start replaces stale projections, and that the
withdrawal overlays (#244/#235) still compose against discovered tools.
"""

import pytest

from mcp_hangar.application.event_handlers.tool_projection_handler import (
    ToolProjectionPopulationHandler,
)
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.events import McpServerStarted, McpServerStopped
from mcp_hangar.domain.model.tool_catalog import ToolCatalog, ToolSchema


def _make_tool(name: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description="A tool",
        input_schema={"type": "object", "properties": {}},
    )


class _StubServer:
    """Minimal stand-in for an McpServer aggregate with a tool catalog."""

    def __init__(self, tools: list[ToolSchema]) -> None:
        catalog = ToolCatalog()
        for t in tools:
            catalog.add(t)
        self.tools = catalog


class _StubRepo:
    def __init__(self, servers: dict[str, _StubServer]) -> None:
        self._servers = servers

    def get(self, mcp_server_id: str):
        return self._servers.get(mcp_server_id)


def _started_event(mcp_server_id: str, tools_count: int) -> McpServerStarted:
    return McpServerStarted(
        mcp_server_id=mcp_server_id,
        mode="subprocess",
        tools_count=tools_count,
        startup_duration_ms=1.0,
    )


@pytest.fixture(autouse=True)
def reset_registry():
    reset_tool_projection_registry()
    yield
    reset_tool_projection_registry()


class TestToolProjectionPopulationHandler:
    def test_populates_registry_on_started(self):
        repo = _StubRepo({"allegro": _StubServer([_make_tool("search"), _make_tool("get_offer")])})
        handler = ToolProjectionPopulationHandler(repository=repo)

        handler.handle(_started_event("allegro", 2))

        registry = get_tool_projection_registry()
        names = {p.tool for p in registry.list_for_server("allegro")}
        assert names == {"search", "get_offer"}
        proj = registry.resolve("allegro", "search", tenant_id=None)
        assert proj is not None
        assert proj.status == "active"
        assert proj.digest is not None

    def test_restart_replaces_stale_projections(self):
        repo_servers = {"allegro": _StubServer([_make_tool("search"), _make_tool("legacy")])}
        repo = _StubRepo(repo_servers)
        handler = ToolProjectionPopulationHandler(repository=repo)
        handler.handle(_started_event("allegro", 2))

        # Re-discovery: legacy tool gone.
        repo_servers["allegro"] = _StubServer([_make_tool("search")])
        handler.handle(_started_event("allegro", 1))

        registry = get_tool_projection_registry()
        names = {p.tool for p in registry.list_for_server("allegro")}
        assert names == {"search"}

    def test_withdrawal_overlay_composes_with_discovered_tool(self):
        registry = get_tool_projection_registry()
        # A config withdrawal registered before discovery.
        registry.set_config_withdrawal("allegro", "search", tenant_id="tenant:openai")

        repo = _StubRepo({"allegro": _StubServer([_make_tool("search")])})
        ToolProjectionPopulationHandler(repository=repo).handle(_started_event("allegro", 1))

        # Discovered AND config-withdrawn for that tenant → resolves withdrawn.
        proj = registry.resolve("allegro", "search", tenant_id="tenant:openai")
        assert proj is not None
        assert proj.is_withdrawn_for("tenant:openai")
        # Other tenant: discovered, active.
        assert not registry.resolve("allegro", "search", tenant_id="tenant:acme").is_withdrawn_for("tenant:acme")

    def test_runtime_withdrawal_composes_with_discovered_tool(self):
        registry = get_tool_projection_registry()
        registry.withdraw("allegro", "search")  # all tenants, runtime

        repo = _StubRepo({"allegro": _StubServer([_make_tool("search")])})
        ToolProjectionPopulationHandler(repository=repo).handle(_started_event("allegro", 1))

        assert registry.resolve("allegro", "search", tenant_id=None).is_withdrawn_for(None)

    def test_unknown_server_is_noop(self):
        handler = ToolProjectionPopulationHandler(repository=_StubRepo({}))
        # Must not raise.
        handler.handle(_started_event("missing", 0))
        assert get_tool_projection_registry().list_for_server("missing") == []

    def test_ignores_non_started_events(self):
        repo = _StubRepo({"allegro": _StubServer([_make_tool("search")])})
        handler = ToolProjectionPopulationHandler(repository=repo)
        handler.handle(McpServerStopped(mcp_server_id="allegro", reason="test"))
        assert get_tool_projection_registry().list_for_server("allegro") == []
