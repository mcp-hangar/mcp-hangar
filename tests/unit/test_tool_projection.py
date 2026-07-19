"""Unit tests for ToolProjection read-model and ToolProjectionRegistry.

Covers the acceptance criteria from issue #230:
- Registry builds projections from discovered backend tools.
- resolve() honors tenant_overrides over base status.
- Cache invalidates on config reload.
- No runtime mutation API is exposed.
"""

import threading

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjection,
    ToolProjectionRegistry,
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.value_objects.tool_digest import ToolDigest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str, description: str = "A tool") -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
    )


def _make_digest(name: str) -> ToolDigest:
    return ToolDigest(tool_name=name, sha256="a" * 64)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """Fresh registry for each test."""
    return ToolProjectionRegistry()


@pytest.fixture(autouse=True)
def reset_global():
    """Ensure the global singleton is clean before and after each test."""
    reset_tool_projection_registry()
    yield
    reset_tool_projection_registry()


# ---------------------------------------------------------------------------
# ToolProjection unit tests
# ---------------------------------------------------------------------------


class TestToolProjection:
    """Tests for the ToolProjection read-model."""

    def test_frozen_dataclass_immutable(self):
        """ToolProjection must be immutable (frozen dataclass)."""
        digest = _make_digest("add")
        proj = ToolProjection(
            mcp_server="math",
            tool="add",
            schema={"name": "add"},
            digest=digest,
        )
        with pytest.raises((AttributeError, TypeError)):
            proj.status = "withdrawn"  # type: ignore[misc]

    def test_default_status_is_active(self):
        """Default status should be 'active'."""
        digest = _make_digest("add")
        proj = ToolProjection(
            mcp_server="math",
            tool="add",
            schema={"name": "add"},
            digest=digest,
        )
        assert proj.status == "active"

    def test_effective_status_no_override(self):
        """effective_status returns base status when no tenant override."""
        digest = _make_digest("add")
        proj = ToolProjection(
            mcp_server="math",
            tool="add",
            schema={"name": "add"},
            digest=digest,
            status="active",
        )
        assert proj.effective_status("tenant-x") == "active"
        assert proj.effective_status(None) == "active"

    def test_effective_status_honors_tenant_override(self):
        """tenant_overrides must take precedence over base status."""
        digest = _make_digest("add")
        proj = ToolProjection(
            mcp_server="math",
            tool="add",
            schema={"name": "add"},
            digest=digest,
            status="active",
            tenant_overrides={"tenant-a": "withdrawn"},
        )
        # Tenant with override sees withdrawn
        assert proj.effective_status("tenant-a") == "withdrawn"
        # Other tenants see the base status
        assert proj.effective_status("tenant-b") == "active"
        assert proj.effective_status(None) == "active"

    def test_is_withdrawn_for_no_override(self):
        """is_withdrawn_for returns False for active tool without override."""
        digest = _make_digest("rm")
        proj = ToolProjection(
            mcp_server="fs",
            tool="rm",
            schema={"name": "rm"},
            digest=digest,
            status="active",
        )
        assert not proj.is_withdrawn_for("tenant-x")

    def test_is_withdrawn_for_base_withdrawn(self):
        """is_withdrawn_for returns True when base status is withdrawn."""
        digest = _make_digest("rm")
        proj = ToolProjection(
            mcp_server="fs",
            tool="rm",
            schema={"name": "rm"},
            digest=digest,
            status="withdrawn",
        )
        assert proj.is_withdrawn_for("any-tenant")
        assert proj.is_withdrawn_for(None)

    def test_is_withdrawn_for_tenant_override_withdrawn(self):
        """Tenant override 'withdrawn' wins over active base status."""
        digest = _make_digest("rm")
        proj = ToolProjection(
            mcp_server="fs",
            tool="rm",
            schema={"name": "rm"},
            digest=digest,
            status="active",
            tenant_overrides={"tenant-a": "withdrawn"},
        )
        assert proj.is_withdrawn_for("tenant-a")
        assert not proj.is_withdrawn_for("tenant-b")

    def test_is_withdrawn_for_tenant_override_active_beats_base_withdrawn(self):
        """Tenant override 'active' wins over withdrawn base status."""
        digest = _make_digest("rm")
        proj = ToolProjection(
            mcp_server="fs",
            tool="rm",
            schema={"name": "rm"},
            digest=digest,
            status="withdrawn",
            tenant_overrides={"tenant-vip": "active"},
        )
        assert not proj.is_withdrawn_for("tenant-vip")
        assert proj.is_withdrawn_for("tenant-regular")


# ---------------------------------------------------------------------------
# ToolProjectionRegistry — build from discovery
# ---------------------------------------------------------------------------


class TestToolProjectionRegistryBuild:
    """Registry builds projections from discovered tools."""

    def test_build_populates_registry(self, registry):
        """build_from_tools should populate the registry."""
        tools = [_make_tool("add"), _make_tool("subtract")]
        registry.build_from_tools("math", tools)

        assert registry.is_built
        assert registry.resolve("math", "add") is not None
        assert registry.resolve("math", "subtract") is not None

    def test_resolve_unknown_tool_returns_none(self, registry):
        """resolve() returns None for unknown (server, tool) pair."""
        tools = [_make_tool("add")]
        registry.build_from_tools("math", tools)

        assert registry.resolve("math", "nonexistent") is None
        assert registry.resolve("unknown-server", "add") is None

    def test_projection_carries_digest(self, registry):
        """Built projection must carry a ToolDigest."""
        tools = [_make_tool("add")]
        registry.build_from_tools("math", tools)

        proj = registry.resolve("math", "add")
        assert proj is not None
        assert isinstance(proj.digest, ToolDigest)
        assert proj.digest.tool_name == "add"
        assert len(proj.digest.sha256) == 64

    def test_projection_mcp_server_and_tool_fields(self, registry):
        """Projection must carry mcp_server and tool name correctly."""
        tools = [_make_tool("query")]
        registry.build_from_tools("sqlite", tools)

        proj = registry.resolve("sqlite", "query")
        assert proj is not None
        assert proj.mcp_server == "sqlite"
        assert proj.tool == "query"

    def test_multiple_servers(self, registry):
        """Registry supports tools from multiple servers."""
        registry.build_from_tools("math", [_make_tool("add")])
        registry.build_from_tools("sqlite", [_make_tool("query")])

        assert registry.resolve("math", "add") is not None
        assert registry.resolve("sqlite", "query") is not None
        assert len(registry.all()) == 2

    def test_rebuild_replaces_server_projections(self, registry):
        """Re-building for a server replaces stale entries atomically."""
        registry.build_from_tools("math", [_make_tool("add"), _make_tool("subtract")])
        assert registry.resolve("math", "subtract") is not None

        # Rebuild with only "add" — "subtract" must disappear
        registry.build_from_tools("math", [_make_tool("add")])
        assert registry.resolve("math", "add") is not None
        assert registry.resolve("math", "subtract") is None

    def test_build_with_status_overrides(self, registry):
        """status_overrides parameter controls base status per tool."""
        tools = [_make_tool("add"), _make_tool("delete")]
        registry.build_from_tools(
            "math",
            tools,
            status_overrides={"delete": "withdrawn"},
        )

        assert registry.resolve("math", "add").status == "active"  # type: ignore[union-attr]
        assert registry.resolve("math", "delete").status == "withdrawn"  # type: ignore[union-attr]

    def test_build_with_tenant_overrides(self, registry):
        """tenant_overrides parameter is stored in the projection."""
        tools = [_make_tool("rm")]
        registry.build_from_tools(
            "fs",
            tools,
            tenant_overrides={"rm": {"tenant-a": "withdrawn"}},
        )

        proj = registry.resolve("fs", "rm")
        assert proj is not None
        assert proj.is_withdrawn_for("tenant-a")
        assert not proj.is_withdrawn_for("tenant-b")

    def test_list_for_server(self, registry):
        """list_for_server returns only projections for the given server."""
        registry.build_from_tools("math", [_make_tool("add"), _make_tool("sub")])
        registry.build_from_tools("sqlite", [_make_tool("query")])

        math_projs = registry.list_for_server("math")
        assert len(math_projs) == 2
        assert all(p.mcp_server == "math" for p in math_projs)

        sqlite_projs = registry.list_for_server("sqlite")
        assert len(sqlite_projs) == 1

    def test_list_for_unknown_server_returns_empty(self, registry):
        """list_for_server returns [] for unknown server."""
        assert registry.list_for_server("unknown") == []


# ---------------------------------------------------------------------------
# ToolProjectionRegistry — tenant-aware resolve
# ---------------------------------------------------------------------------


class TestToolProjectionRegistryResolve:
    """resolve() must honor tenant_overrides over base status."""

    def test_resolve_returns_projection_with_correct_schema(self, registry):
        """resolve() returns a projection containing the tool schema."""
        tools = [_make_tool("add", description="Add two numbers")]
        registry.build_from_tools("math", tools)

        proj = registry.resolve("math", "add")
        assert proj is not None
        assert proj.schema.get("name") == "add"

    def test_resolve_tenant_id_informational(self, registry):
        """resolve() accepts tenant_id; the returned projection carries overrides."""
        tools = [_make_tool("rm")]
        registry.build_from_tools(
            "fs",
            tools,
            tenant_overrides={"rm": {"tenant-a": "withdrawn"}},
        )

        proj = registry.resolve("fs", "rm", tenant_id="tenant-a")
        assert proj is not None
        assert proj.is_withdrawn_for("tenant-a")

    def test_resolve_active_tool_not_withdrawn(self, registry):
        """resolve() for a purely active tool: is_withdrawn_for returns False."""
        registry.build_from_tools("math", [_make_tool("add")])

        proj = registry.resolve("math", "add", tenant_id="any")
        assert proj is not None
        assert not proj.is_withdrawn_for("any")


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


class TestToolProjectionRegistryInvalidation:
    """Cache invalidates on config reload (invalidate())."""

    def test_invalidate_clears_all_projections(self, registry):
        """invalidate() must clear all projections."""
        registry.build_from_tools("math", [_make_tool("add")])
        assert registry.resolve("math", "add") is not None

        registry.invalidate()

        assert not registry.is_built
        assert registry.resolve("math", "add") is None
        assert registry.all() == []

    def test_rebuild_after_invalidate(self, registry):
        """Registry can be rebuilt after invalidation."""
        registry.build_from_tools("math", [_make_tool("add")])
        registry.invalidate()
        registry.build_from_tools("math", [_make_tool("add"), _make_tool("subtract")])

        assert registry.is_built
        assert registry.resolve("math", "subtract") is not None

    def test_invalidate_then_resolve_unknown(self, registry):
        """After invalidate, resolve returns None for previously known tools."""
        registry.build_from_tools("sqlite", [_make_tool("query")])
        registry.invalidate()

        assert registry.resolve("sqlite", "query") is None

    def test_is_built_false_before_first_build(self):
        """is_built is False before any build_from_tools call."""
        r = ToolProjectionRegistry()
        assert not r.is_built


# ---------------------------------------------------------------------------
# Mutation API surface (runtime withdrawal — issue #235)
# ---------------------------------------------------------------------------


class TestMutationApi:
    """Registry exposes runtime mutation methods as of #235 (withdraw/restore).

    set_status() and update_projection() remain unexposed — only the
    targeted runtime-withdrawal overlay is added.
    """

    def test_withdraw_method_exists(self, registry):
        """withdraw() must exist on the registry (added by #235)."""
        assert hasattr(registry, "withdraw")

    def test_restore_method_exists(self, registry):
        """restore() must exist on the registry (added by #235)."""
        assert hasattr(registry, "restore")

    def test_no_set_status_method(self, registry):
        """set_status() must not exist on the registry."""
        assert not hasattr(registry, "set_status")

    def test_no_update_method(self, registry):
        """update() / update_projection() must not exist on the registry."""
        assert not hasattr(registry, "update")
        assert not hasattr(registry, "update_projection")


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------


class TestToolProjectionRegistryThreadSafety:
    """Basic thread-safety smoke test."""

    def test_concurrent_build_and_resolve(self):
        """Concurrent build_from_tools and resolve must not raise."""
        reg = ToolProjectionRegistry()
        errors = []

        def build():
            try:
                for i in range(20):
                    reg.build_from_tools("math", [_make_tool(f"tool_{i}")])
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def resolve():
            try:
                for _ in range(50):
                    reg.resolve("math", "tool_0")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=build)] + [threading.Thread(target=resolve) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


class TestSingletonAccessor:
    """get_tool_projection_registry() returns the process-global singleton."""

    def test_singleton_returns_same_instance(self):
        """Multiple calls return the same instance."""
        r1 = get_tool_projection_registry()
        r2 = get_tool_projection_registry()
        assert r1 is r2

    def test_reset_gives_fresh_instance(self):
        """reset_tool_projection_registry() gives a fresh instance."""
        r1 = get_tool_projection_registry()
        reset_tool_projection_registry()
        r2 = get_tool_projection_registry()
        assert r1 is not r2

    def test_singleton_accessible_via_domain_services(self):
        """Singleton is accessible from mcp_hangar.domain.services."""
        from mcp_hangar.domain import services

        fn = services.get_tool_projection_registry
        assert callable(fn)
        assert fn() is get_tool_projection_registry()
