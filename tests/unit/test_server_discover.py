"""Unit tests for the SEP-2575 ``server/discover`` entry point (issue #290).

Covers: DiscoverResult shape; the tenant-scoped ``tools`` surface equals the
tenant's ``tools/list`` projection; tenant isolation (A never sees B's tools,
via withdrawal AND member-scope policy); the HTTP handler scopes by identity
context; JSON-RPC POST envelope; and factory wiring.

Naming: neutral placeholders only (server_a, read_item/get_item/delete_item/
secret_tool, tenant:a / tenant:b).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import flat_tool_projection, server_discover
from mcp_hangar._sdk_compat import lowlevel_server
from mcp_hangar.fastmcp_server.server_discover import server_discover_result, tenant_scoped_tools

_PROJ_PATH = "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry"
_RESOLVER_PATH = "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver"


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


def _populate(registry: ToolProjectionRegistry, server: str, tools: list[str]) -> None:
    registry.build_from_tools(
        server,
        [ToolSchema(name=t, description=f"Does {t}", input_schema={"type": "object", "properties": {}}) for t in tools],
    )


@pytest.fixture(autouse=True)
def clean_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture
def registry() -> ToolProjectionRegistry:
    return ToolProjectionRegistry()


@pytest.fixture
def resolver() -> ToolAccessResolver:
    r = ToolAccessResolver()
    r.set_topology_mode("front_door")
    return r


@contextmanager
def _wired(registry, resolver):
    with patch(_PROJ_PATH, return_value=registry), patch(_RESOLVER_PATH, return_value=resolver):
        yield


def _names(tools: list[dict]) -> set[str]:
    return {t["name"] for t in tools}


# ---------------------------------------------------------------------------
# Result shape / content parity with tools/list
# ---------------------------------------------------------------------------


class TestDiscoverResultShape:
    def test_result_has_sep2575_fields(self, registry, resolver):
        _populate(registry, "server_a", ["read_item"])
        with _wired(registry, resolver):
            result = server_discover_result("tenant:a")

        assert result["supportedVersions"]  # non-empty list of protocol versions
        assert result["capabilities"]["tools"]["listChanged"] is True
        assert result["serverInfo"]["name"] == "mcp-hangar"
        assert "version" in result["serverInfo"]
        assert isinstance(result["tools"], list)

    def test_tools_surface_matches_tools_list_projection(self, registry, resolver):
        """The discover ``tools`` surface equals the tenant's tools/list projection."""
        _populate(registry, "server_a", ["read_item", "get_item"])
        with _wired(registry, resolver):
            discover_tools = tenant_scoped_tools("tenant:a")
            # Reproduce exactly what the tools/list projection path produces.
            flat_map = flat_tool_projection._build_flat_map("tenant:a")
            list_tools = [
                t.model_dump(mode="json", by_alias=True, exclude_none=True)
                for t in flat_tool_projection._build_mcp_tool_list(flat_map)
            ]

        assert discover_tools == list_tools
        assert _names(discover_tools) == {"read_item", "get_item"}


# ---------------------------------------------------------------------------
# Tenant isolation — the load-bearing guarantee
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_withdrawal_isolates_a_from_b(self, registry, resolver):
        """secret_tool withdrawn for tenant:a is absent for A but present for B."""
        _populate(registry, "server_a", ["read_item", "secret_tool"])
        registry.withdraw("server_a", "secret_tool", tenant_id="tenant:a")
        with _wired(registry, resolver):
            names_a = _names(tenant_scoped_tools("tenant:a"))
            names_b = _names(tenant_scoped_tools("tenant:b"))

        assert "secret_tool" not in names_a
        assert "secret_tool" in names_b
        assert "read_item" in names_a and "read_item" in names_b

    def test_member_policy_isolates_a_from_b(self, registry, resolver):
        """A tool denied for tenant:b by member policy is invisible to B, visible to A."""
        _populate(registry, "server_a", ["read_item", "delete_item"])
        resolver.set_standalone_member_policy("server_a", "tenant:b", ToolAccessPolicy(deny_list=("delete_item",)))
        with _wired(registry, resolver):
            names_a = _names(tenant_scoped_tools("tenant:a"))
            names_b = _names(tenant_scoped_tools("tenant:b"))

        assert "delete_item" in names_a
        assert "delete_item" not in names_b

    @pytest.mark.asyncio
    async def test_handler_scopes_by_identity_context(self, registry, resolver):
        """The GET handler returns only the surface for the tenant bound in context."""
        _populate(registry, "server_a", ["read_item", "secret_tool"])
        registry.withdraw("server_a", "secret_tool", tenant_id="tenant:a")

        req = Mock()
        req.method = "GET"

        async def surface(tenant_id: str | None) -> set[str]:
            token = identity_context_var.set(_identity(tenant_id))
            try:
                with _wired(registry, resolver):
                    resp = await server_discover.server_discover_handler(req)
            finally:
                identity_context_var.reset(token)
            return _names(json.loads(bytes(resp.body).decode())["tools"])

        assert "secret_tool" not in await surface("tenant:a")
        assert "secret_tool" in await surface("tenant:b")


# ---------------------------------------------------------------------------
# HTTP handler envelope + factory wiring
# ---------------------------------------------------------------------------


class TestHandlerAndWiring:
    @pytest.mark.asyncio
    async def test_post_returns_jsonrpc_result(self, registry, resolver):
        _populate(registry, "server_a", ["read_item"])
        req = Mock()
        req.method = "POST"
        req.json = _make_awaitable({"jsonrpc": "2.0", "id": 7, "method": "server/discover", "params": {}})

        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with _wired(registry, resolver):
                resp = await server_discover.server_discover_handler(req)
        finally:
            identity_context_var.reset(token)

        body = json.loads(bytes(resp.body).decode())
        assert body["jsonrpc"] == "2.0" and body["id"] == 7
        assert body["result"]["serverInfo"]["name"] == "mcp-hangar"
        assert _names(body["result"]["tools"]) == {"read_item"}

    @pytest.mark.asyncio
    async def test_post_wrong_method_is_method_not_found(self, registry, resolver):
        req = Mock()
        req.method = "POST"
        req.json = _make_awaitable({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        with _wired(registry, resolver):
            resp = await server_discover.server_discover_handler(req)

        assert resp.status_code == 404
        assert json.loads(bytes(resp.body).decode())["error"]["code"] == -32601

    def test_factory_registers_route(self):
        from mcp_hangar.fastmcp_server import HangarFunctions, MCPServerFactory

        hangar = HangarFunctions(
            list=Mock(return_value={"mcp_servers": []}),
            start=Mock(),
            stop=Mock(),
            invoke=Mock(),
            tools=Mock(),
            details=Mock(),
            health=Mock(return_value={"status": "healthy"}),
        )
        with patch("mcp_hangar.fastmcp_server.server_discover.register_server_discover") as mock_register:
            mcp = MCPServerFactory(hangar).create_server()
            mock_register.assert_called_once_with(mcp)


def _make_awaitable(value):
    async def _coro():
        return value

    return _coro


# ---------------------------------------------------------------------------
# The discovery result must describe the REAL server (#605, #606)
# ---------------------------------------------------------------------------


class TestDiscoverDescribesTheRealServer:
    """A stateless client has no `initialize`; this endpoint is all it gets.

    While the result was assembled from literals, a modern client was told the
    gateway had no Tasks, no prompts, no resources (#605), and — on an egress
    gateway whose backend had not started — no tools at all (#606). Both were
    contradicted by `initialize` on the same process.
    """

    def _server_with_tools(self):
        from mcp_hangar._sdk_compat import new_mcp_server

        mcp = new_mcp_server("test-server", version="9.9.9")

        @mcp.tool(name="hangar_demo")
        def hangar_demo(x: int) -> int:
            """A meta-API tool."""
            return x

        return mcp

    def test_capabilities_come_from_the_server_not_a_literal(self, registry, resolver):
        mcp = self._server_with_tools()

        result = server_discover_result(None, mcp)

        # Whatever the server reports, discover reports -- byte for byte.
        expected = lowlevel_server(mcp).get_capabilities().model_dump(mode="json", by_alias=True, exclude_none=True)
        assert result["capabilities"] == expected
        # And it is richer than the old literal: the handshake surface has more
        # than tools on it.
        assert set(result["capabilities"]) > {"tools"}

    def test_capabilities_track_the_advertised_tasks_extension(self, registry, resolver):
        """The concrete #605 symptom: Tasks advertised on the server, absent here.

        Asserts the EXTENSION entry, not `capabilities.tasks`. The latter has no
        field in `v2026_07_28.ServerCapabilities`, so the SDK's per-version sieve
        drops it from a modern discover -- a server advertising it there would
        serve `tasks/*` that no spec-following client could discover.
        """
        from mcp_hangar._sdk_compat import HAS_NATIVE_TASKS
        from mcp_hangar.fastmcp_server.task_relay_wiring import advertise_tasks_capability

        if not HAS_NATIVE_TASKS:
            pytest.skip("SDK without the native Tasks extension")

        mcp = self._server_with_tools()
        advertise_tasks_capability(mcp, relay_tasks_enabled=True)

        result = server_discover_result(None, mcp)

        from mcp_hangar.tasks_wire import EXTENSION_ID

        assert EXTENSION_ID in (result["capabilities"].get("extensions") or {}), (
            "the relay advertises the tasks extension; discover must not hide it"
        )

    def test_egress_advertises_the_meta_api_even_with_no_backend_started(self, registry, resolver):
        """#606: an empty projection must not mean an empty discovery result."""
        mcp = self._server_with_tools()

        with _wired(registry, resolver):  # projection registry is empty here
            result = server_discover_result(None, mcp)

        assert _names(result["tools"]) == {"hangar_demo"}, result["tools"]

    def test_front_door_still_advertises_the_flat_tenant_projection(self, registry, resolver):
        """Topology decides which surface tools/list serves; discover must agree."""
        _populate(registry, "server_a", ["read_item"])
        mcp = self._server_with_tools()

        # Topology in production comes from the process-wide resolver, which is
        # what `_maybe_register_flat_tool_handlers` consults, so set that rather
        # than a patched stand-in. `clean_singletons` resets it afterwards.
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        get_tool_access_resolver().set_topology_mode("front_door")

        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with _wired(registry, resolver):
                result = server_discover_result("tenant:a", mcp)
        finally:
            identity_context_var.reset(token)

        names = _names(result["tools"])
        assert "read_item" in names, names
        assert "hangar_demo" not in names, "front_door must not leak the meta-API"

    def test_a_capability_read_failure_degrades_instead_of_failing_discovery(self, registry, resolver):
        """Discovery answering something honest beats it 500-ing."""
        broken = Mock()
        broken._mcp_server.get_capabilities.side_effect = RuntimeError("boom")
        broken._tool_manager.list_tools.side_effect = RuntimeError("boom")

        result = server_discover_result(None, broken)

        assert result["capabilities"] == {"tools": {"listChanged": True}}
        assert result["tools"] == []
