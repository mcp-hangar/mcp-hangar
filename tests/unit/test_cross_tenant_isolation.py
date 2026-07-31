"""Regression test for cross-tenant tool isolation (issue #312, interpretation A).

Interpretation A (chosen): the token ``aud`` binds to a single, *global* Hangar
resource server (RFC 8707 already validates that the token was issued FOR the
Hangar RS). The real cross-tenant boundary is the ``tenant_id`` JWT claim plus
the per-tenant tool projection / member-scope policy (issues #237 / #241), NOT
the audience.

This file proves that boundary at the projection read-model and the
member-scope resolver / executor layers, without spinning up a full server:

- Projection layer: ``_build_flat_map`` never surfaces another tenant's tool.
- Resolver layer: ``is_tool_allowed`` denies a tenant's access to a tool that
  belongs to a different tenant's visibility set.
- Invoke layer: a caller carrying ``tenant_id="A"`` that attempts to invoke
  tenant B's tool is rejected with ``ToolAccessDeniedError`` and the backend is
  never reached (``command_bus.send`` is not called).

Naming: neutral placeholders only.
  server  -> shared_backend (one shared RS, per interpretation A)
  tools   -> tenant_a_tool, tenant_b_tool
  tenants -> tenant:a, tenant:b
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services import get_tool_access_resolver
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server.flat_tool_projection import _build_flat_map
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.batch.models import BatchResult

# ---------------------------------------------------------------------------
# Constants — one shared RS, two tenants, one private tool each.
# ---------------------------------------------------------------------------

SERVER = "shared_backend"
TOOL_A = "tenant_a_tool"
TOOL_B = "tenant_b_tool"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"


# ---------------------------------------------------------------------------
# Helpers (reused patterns from test_flat_tool_reexport / test_member_scope_policy)
# ---------------------------------------------------------------------------


def _make_identity(tenant_id: str | None) -> IdentityContext:
    caller = CallerIdentity(
        user_id=None,
        agent_id=None,
        session_id=None,
        principal_type="anonymous",
        tenant_id=tenant_id,
    )
    return IdentityContext(caller=caller)


def _make_schema(name: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=f"Does {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )


def _populate_registry(registry: ToolProjectionRegistry, server: str, tools: list[str]) -> None:
    registry.build_from_tools(server, [_make_schema(t) for t in tools])


def _configure_two_tenants(registry: ToolProjectionRegistry, resolver: ToolAccessResolver) -> None:
    """Shared backend exports both tools; each tenant may see only its own.

    Distinct per-tenant visibility is expressed with an allow-list member-scope
    policy — the production mechanism that enforces cross-tenant separation.
    """
    _populate_registry(registry, SERVER, [TOOL_A, TOOL_B])
    resolver.set_standalone_member_policy(SERVER, TENANT_A, ToolAccessPolicy(allow_list=(TOOL_A,)))
    resolver.set_standalone_member_policy(SERVER, TENANT_B, ToolAccessPolicy(allow_list=(TOOL_B,)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_singletons():
    """Reset the global projection registry and access resolver around each test."""
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture
def registry() -> ToolProjectionRegistry:
    """Fresh ToolProjectionRegistry (not the process-global singleton)."""
    return ToolProjectionRegistry()


@pytest.fixture
def resolver() -> ToolAccessResolver:
    """Fresh ToolAccessResolver representing the shared front_door RS."""
    r = ToolAccessResolver()
    r.set_topology_mode("front_door")
    return r


@pytest.fixture
def mock_context():
    """Minimal application context mock required by BatchExecutor."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.mcp_server_exists.return_value = True

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield ctx


# ---------------------------------------------------------------------------
# Projection read-model: a tenant's flat map excludes the other tenant's tool.
# ---------------------------------------------------------------------------


class TestCrossTenantProjectionIsolation:
    """The per-tenant projection read-model never leaks another tenant's tools."""

    def _flat_map_for(
        self,
        tenant_id: str,
        registry: ToolProjectionRegistry,
        resolver: ToolAccessResolver,
    ) -> dict[str, tuple[str, str]]:
        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            return _build_flat_map(tenant_id)

    def test_tenant_a_projection_excludes_tenant_b_tool(self, registry, resolver):
        """tenant:a sees its own tool and NOT tenant:b's tool."""
        _configure_two_tenants(registry, resolver)

        flat_a = self._flat_map_for(TENANT_A, registry, resolver)

        assert TOOL_A in flat_a
        # Tenant-boundary assertion: B's tool must be absent from A's projection.
        assert TOOL_B not in flat_a

    def test_tenant_b_projection_excludes_tenant_a_tool(self, registry, resolver):
        """tenant:b sees its own tool and NOT tenant:a's tool (symmetric)."""
        _configure_two_tenants(registry, resolver)

        flat_b = self._flat_map_for(TENANT_B, registry, resolver)

        assert TOOL_B in flat_b
        assert TOOL_A not in flat_b

    def test_two_tenants_receive_disjoint_projections(self, registry, resolver):
        """The two tenants' visible tool sets do not overlap."""
        _configure_two_tenants(registry, resolver)

        flat_a = self._flat_map_for(TENANT_A, registry, resolver)
        flat_b = self._flat_map_for(TENANT_B, registry, resolver)

        assert set(flat_a) == {TOOL_A}
        assert set(flat_b) == {TOOL_B}
        assert set(flat_a).isdisjoint(set(flat_b))


# ---------------------------------------------------------------------------
# Member-scope resolver: cross-tenant access is denied at the policy layer.
# ---------------------------------------------------------------------------


class TestCrossTenantPolicyResolver:
    """The member-scope resolver denies a tenant access to another tenant's tool."""

    def test_resolver_denies_cross_tenant_tool(self, resolver):
        """A's identity is allowed A's tool but denied B's tool, and vice versa."""
        resolver.set_standalone_member_policy(SERVER, TENANT_A, ToolAccessPolicy(allow_list=(TOOL_A,)))
        resolver.set_standalone_member_policy(SERVER, TENANT_B, ToolAccessPolicy(allow_list=(TOOL_B,)))

        # Own tool: allowed.
        assert resolver.is_tool_allowed(SERVER, TOOL_A, member_id=TENANT_A) is True
        assert resolver.is_tool_allowed(SERVER, TOOL_B, member_id=TENANT_B) is True

        # Cross-tenant: the exact boundary — A cannot reach B's tool, B cannot reach A's.
        assert resolver.is_tool_allowed(SERVER, TOOL_B, member_id=TENANT_A) is False
        assert resolver.is_tool_allowed(SERVER, TOOL_A, member_id=TENANT_B) is False


# ---------------------------------------------------------------------------
# Invoke path: invoking another tenant's tool under A's identity is rejected.
# ---------------------------------------------------------------------------


class TestCrossTenantInvokeRejection:
    """BatchExecutor rejects an attempt to invoke another tenant's tool."""

    def _execute(self, tool: str) -> BatchResult:
        executor = BatchExecutor()
        return executor.execute(
            batch_id="xtenant",
            calls=[
                CallSpec(
                    index=0,
                    call_id="xtenant-0",
                    mcp_server=SERVER,
                    tool=tool,
                    arguments={},
                )
            ],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )

    def test_invoke_other_tenant_tool_is_rejected(self, mock_context):
        """tenant:a invoking tenant:b's tool → ToolAccessDeniedError, backend untouched."""
        resolver = get_tool_access_resolver()
        resolver.set_standalone_member_policy(SERVER, TENANT_A, ToolAccessPolicy(allow_list=(TOOL_A,)))
        resolver.set_standalone_member_policy(SERVER, TENANT_B, ToolAccessPolicy(allow_list=(TOOL_B,)))

        token = identity_context_var.set(_make_identity(TENANT_A))
        try:
            result = self._execute(TOOL_B)
        finally:
            identity_context_var.reset(token)

        # Tenant-boundary assertion at the invoke path: the call is denied and
        # never reaches the backend command bus.
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolAccessDeniedError"
        mock_context.command_bus.send.assert_not_called()

    def test_invoke_own_tool_succeeds(self, mock_context):
        """Sanity: tenant:a invoking its OWN tool is allowed and reaches the backend."""
        resolver = get_tool_access_resolver()
        resolver.set_standalone_member_policy(SERVER, TENANT_A, ToolAccessPolicy(allow_list=(TOOL_A,)))
        resolver.set_standalone_member_policy(SERVER, TENANT_B, ToolAccessPolicy(allow_list=(TOOL_B,)))

        token = identity_context_var.set(_make_identity(TENANT_A))
        try:
            result = self._execute(TOOL_A)
        finally:
            identity_context_var.reset(token)

        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()
