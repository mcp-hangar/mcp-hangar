"""End-to-end test of the stateless front door (issue #338).

This test wires the SHIPPED front-door pieces together and proves the three
guarantees a two-tenant deployment relies on, exercised through the real
projection path (no bespoke test infra, no live network):

* Per-tenant tool projection isolation (#312 / #232): tenant A and tenant B are
  served DISJOINT flat tool lists from a single shared backend -- neither ever
  sees the other's tool.  Enforced via the shipped ``register_flat_tool_handlers``
  ``tools/list`` handler + ``_build_flat_map`` + the member-scope resolver.
* Per-tenant SEP-2549 ``cacheScope`` (#292): each tenant's projected
  ``tools/list`` carries a DISTINCT, stable ``cacheScope`` in the result
  ``_meta``; an unknown/None tenant FAILS CLOSED to a narrowest, non-shareable
  ``no-store`` token (never a shared/global constant).
* Stateless routing (#336 / #377): the front-door router resolves both tenants'
  requests from ``Mcp-Method`` / ``Mcp-Name`` headers with NO ``Mcp-Session-Id``,
  and the presence of a session id does not change the decision.  Serving the
  list needs no session id either -- the handler reads only the tenant identity.

Naming: neutral placeholders only.
  server  -> shared_backend (one shared RS fronting both tenants)
  tools   -> alpha_read (tenant A), beta_write (tenant B)
  tenants -> tenant:a, tenant:b
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

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
from mcp_hangar.fastmcp_server.flat_tool_projection import (
    CACHE_SCOPE_META_KEY,
    CACHE_TTL_META_KEY,
    PROJECTED_LIST_CACHE_TTL_MS,
    register_flat_tool_handlers,
)
from mcp_hangar.fastmcp_server.front_door_routing import (
    FrontDoorRoutingMiddleware,
    RouteDecision,
)

# --------------------------------------------------------------------------- #
# Constants -- one shared backend, two tenants, one private tool each.
# --------------------------------------------------------------------------- #

SERVER = "shared_backend"
TOOL_A = "alpha_read"
TOOL_B = "beta_write"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"


# --------------------------------------------------------------------------- #
# Helpers (reused patterns from test_flat_tool_reexport / test_cross_tenant_*)
# --------------------------------------------------------------------------- #


def _make_identity(tenant_id: str | None) -> IdentityContext:
    """A stateless caller identity -- note session_id is None (no session)."""
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
    """Shared backend exports BOTH tools; each tenant may see only its own.

    Per-tenant visibility is expressed with an allow-list member-scope policy --
    the production mechanism that enforces cross-tenant separation (#237 / #241).
    """
    _populate_registry(registry, SERVER, [TOOL_A, TOOL_B])
    resolver.set_standalone_member_policy(SERVER, TENANT_A, ToolAccessPolicy(allow_list=(TOOL_A,)))
    resolver.set_standalone_member_policy(SERVER, TENANT_B, ToolAccessPolicy(allow_list=(TOOL_B,)))


def _capture_list_handler(registry: ToolProjectionRegistry, resolver: ToolAccessResolver):
    """Register the shipped flat handlers on a mock FastMCP; return the list fn.

    Mirrors the seam used by ``register_flat_tool_handlers``: it re-registers the
    lowlevel ``list_tools`` / ``call_tool`` handlers via the decorators exposed on
    ``mcp._mcp_server``.  We capture the async ``tools/list`` handler so the test
    can invoke exactly the code path a real request would.
    """
    mcp_mock = MagicMock()
    captured: dict[str, Any] = {}

    def fake_list_tools():
        def decorator(fn):
            captured["list"] = fn
            return fn

        return decorator

    def fake_call_tool(*, validate_input=True):
        def decorator(fn):
            captured["call"] = fn
            return fn

        return decorator

    mcp_mock._mcp_server.list_tools = fake_list_tools
    mcp_mock._mcp_server.call_tool = fake_call_tool

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
        register_flat_tool_handlers(mcp_mock)

    return captured["list"]


async def _serve_list(
    list_fn,
    tenant_id: str | None,
    registry: ToolProjectionRegistry,
    resolver: ToolAccessResolver,
):
    """Serve one projected ``tools/list`` for *tenant_id* -- no session id anywhere.

    Returns the shipped ``ListToolsResult`` (tools + SEP-2549 ``_meta``).
    """
    # A None tenant models the unknown/unauthenticated caller (identity absent).
    identity = _make_identity(tenant_id) if tenant_id is not None else None
    token = identity_context_var.set(identity)
    try:
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
            return await list_fn()
    finally:
        identity_context_var.reset(token)


def _tool_names(result: Any) -> set[str]:
    return {t.name for t in result.tools}


def _cache_scope(result: Any) -> str:
    meta = result.meta  # pydantic exposes the ``_meta`` alias as ``.meta``
    assert isinstance(meta, dict)
    scope = meta[CACHE_SCOPE_META_KEY]
    assert isinstance(scope, str)
    return scope


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# End-to-end: two-tenant isolation + per-tenant cacheScope, served statelessly.
# --------------------------------------------------------------------------- #


class TestE2EStatelessFrontDoorProjection:
    """Both tenants are served from ONE shared backend with no session affinity."""

    @pytest.mark.asyncio
    async def test_two_tenants_get_disjoint_tool_lists(self, registry, resolver):
        """tenant A sees only A's tool, tenant B only B's -- no cross-tenant leak."""
        _configure_two_tenants(registry, resolver)
        list_fn = _capture_list_handler(registry, resolver)

        result_a = await _serve_list(list_fn, TENANT_A, registry, resolver)
        result_b = await _serve_list(list_fn, TENANT_B, registry, resolver)

        names_a = _tool_names(result_a)
        names_b = _tool_names(result_b)

        # Each tenant sees exactly its own tool ...
        assert names_a == {TOOL_A}
        assert names_b == {TOOL_B}
        # ... the other tenant's tool is absent (the isolation boundary) ...
        assert TOOL_B not in names_a
        assert TOOL_A not in names_b
        # ... and the two visible sets do not overlap.
        assert names_a.isdisjoint(names_b)
        # No control-plane surface leaks through the flat projection either.
        assert not any(n.startswith("hangar_") for n in names_a | names_b)

    @pytest.mark.asyncio
    async def test_two_tenants_get_distinct_cache_scope(self, registry, resolver):
        """Each tenant's list carries a DISTINCT cacheScope in _meta; same tenant is stable."""
        _configure_two_tenants(registry, resolver)
        list_fn = _capture_list_handler(registry, resolver)

        result_a = await _serve_list(list_fn, TENANT_A, registry, resolver)
        result_b = await _serve_list(list_fn, TENANT_B, registry, resolver)
        result_a_again = await _serve_list(list_fn, TENANT_A, registry, resolver)

        scope_a = _cache_scope(result_a)
        scope_b = _cache_scope(result_b)
        scope_a_again = _cache_scope(result_a_again)

        # Distinct per tenant: a naive cache keyed on cacheScope can never serve
        # tenant A's list to tenant B.
        assert scope_a != scope_b
        # Stable per tenant: the same tenant twice gets the SAME scope (cacheable
        # within the tenant).
        assert scope_a == scope_a_again
        # The advertised scope must not embed the raw tenant identifier.
        assert TENANT_A not in scope_a
        assert TENANT_B not in scope_b

    @pytest.mark.asyncio
    async def test_list_meta_carries_conservative_ttl(self, registry, resolver):
        """The projected list advertises the SEP-2549 ttlMs freshness hint."""
        _configure_two_tenants(registry, resolver)
        list_fn = _capture_list_handler(registry, resolver)

        result = await _serve_list(list_fn, TENANT_A, registry, resolver)

        assert result.meta[CACHE_TTL_META_KEY] == PROJECTED_LIST_CACHE_TTL_MS
        assert result.meta[CACHE_TTL_META_KEY] > 0

    @pytest.mark.asyncio
    async def test_unknown_tenant_fails_closed(self, registry, resolver):
        """No identity -> empty list AND a narrowest, non-shareable no-store scope."""
        _configure_two_tenants(registry, resolver)
        list_fn = _capture_list_handler(registry, resolver)

        result_none_1 = await _serve_list(list_fn, None, registry, resolver)
        result_none_2 = await _serve_list(list_fn, None, registry, resolver)

        # Fail closed on tools: an unauthenticated caller sees NOTHING in
        # front_door mode (deny-all), so neither tenant's tool leaks.
        assert _tool_names(result_none_1) == set()

        scope_none_1 = _cache_scope(result_none_1)
        scope_none_2 = _cache_scope(result_none_2)

        # Non-shareable: a fresh token every request, so a cache never gets a
        # second hit on it (structurally uncacheable).
        assert scope_none_1 != scope_none_2
        # And it can never equal any real tenant's shareable scope.
        result_a = await _serve_list(list_fn, TENANT_A, registry, resolver)
        result_b = await _serve_list(list_fn, TENANT_B, registry, resolver)
        assert scope_none_1 != _cache_scope(result_a)
        assert scope_none_1 != _cache_scope(result_b)


# --------------------------------------------------------------------------- #
# Stateless routing: both tenants routed with NO Mcp-Session-Id (#336 / #377).
# --------------------------------------------------------------------------- #


def _scope(headers: dict[str, str]) -> dict[str, Any]:
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": raw, "state": {}}


def _receive_for(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


class _CapturingApp:
    """Downstream ASGI app that records the route decision the middleware stashed."""

    def __init__(self) -> None:
        self.called = False
        self.seen_scope: dict[str, Any] | None = None

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        self.seen_scope = scope
        more = True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                break
            more = msg.get("more_body", False)


async def _route(headers: dict[str, str]) -> tuple[_CapturingApp, RouteDecision]:
    app = _CapturingApp()
    mw = FrontDoorRoutingMiddleware(app, mcp_path="/mcp")

    async def _send(_message) -> None:  # pragma: no cover - no rejection expected
        raise AssertionError("stateless list request must not be rejected")

    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
    await mw(_scope(headers), _receive_for(body), _send)
    assert app.seen_scope is not None
    decision = app.seen_scope["state"]["mcp_route"]
    assert isinstance(decision, RouteDecision)
    return app, decision


class TestE2EStatelessRouting:
    """The front door routes tools/list for both tenants without any session id."""

    @pytest.mark.asyncio
    async def test_both_tenants_route_tools_list_without_session_id(self):
        """A tools/list request routes on headers alone -- no Mcp-Session-Id present."""
        # Two tenants hit the SAME stateless endpoint; the router decision depends
        # only on Mcp-Method / Mcp-Name, never on a session.
        headers = {"mcp-method": "tools/list", "content-type": "application/json"}
        assert "mcp-session-id" not in {k.lower() for k in headers}

        app, decision = await _route(headers)

        assert app.called is True
        assert decision.method == "tools/list"
        assert decision.source == "header"

    @pytest.mark.asyncio
    async def test_session_id_header_does_not_change_routing(self):
        """Presence of an Mcp-Session-Id must not alter the stateless route decision."""
        base = {"mcp-method": "tools/list", "content-type": "application/json"}
        _, without_sid = await _route(base)
        _, with_sid = await _route({**base, "mcp-session-id": "irrelevant-123"})

        assert without_sid == with_sid

    @pytest.mark.asyncio
    async def test_route_then_serve_end_to_end_without_session(self, registry, resolver):
        """Full path: route (no session id) then serve each tenant's isolated list."""
        _configure_two_tenants(registry, resolver)
        list_fn = _capture_list_handler(registry, resolver)

        # 1. Stateless routing: the request carries no session id.
        _, decision = await _route({"mcp-method": "tools/list"})
        assert decision.method == "tools/list"

        # 2. Serving: the identity carries no session id either (session_id=None);
        #    the projection depends only on the tenant.  Each tenant still gets its
        #    own isolated list with its own distinct cacheScope.
        result_a = await _serve_list(list_fn, TENANT_A, registry, resolver)
        result_b = await _serve_list(list_fn, TENANT_B, registry, resolver)

        assert _tool_names(result_a) == {TOOL_A}
        assert _tool_names(result_b) == {TOOL_B}
        assert _cache_scope(result_a) != _cache_scope(result_b)
