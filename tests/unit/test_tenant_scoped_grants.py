"""A role bound at tenant scope is a grant within that tenant, and nowhere else.

The authorizer merged ``tenant:<id>`` bindings into the same flat set as global
ones and answered "allowed" for either, while every control-plane check is made
against ``resource_id="*"``. A grant meant for one tenant therefore opened each
route it named for every tenant. Now:

* the authorizer reports the scope of the grant that matched;
* the REST/WebSocket route guard and the MCP management-tool gate refuse a
  tenant-scoped grant unless the rule is explicitly tenant-aware;
* the tenant-aware handlers confine what they serve or change to that tenant.

This file is the unit-level half: the authorizer, the value objects, the route
table and the guard driven over EVERY rule, and the MCP gate over every tool.
The served-stack regression tests -- real API keys, the real role store, the app
``serve --http`` builds -- are in
``tests/integration/test_tenant_scoped_grants_on_served_app.py``.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.datastructures import State
from starlette.requests import HTTPConnection

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.opa_authorizer import CombinedAuthorizer
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, AuthorizationResult, GrantScope
from mcp_hangar.domain.events import ToolWithdrawn
from mcp_hangar.domain.events.invocation import ToolInvocationFailed
from mcp_hangar.domain.events.tenancy import event_tenant_id
from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.security import Permission, Principal, PrincipalId, PrincipalType, Role
from mcp_hangar.server.api.middleware import AuthorizationEnforcementMiddleware
from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS, RouteRule, resolve_rule
from mcp_hangar.server.api.tenant_scope import confined_tenant, record_grant_scope
from mcp_hangar.server.api.ws.filters import visible_to_tenant
from mcp_hangar.server.tools.tool_permissions import (
    INVOKE_PATH_TOOLS,
    SELF_AUTHORIZING_TOOLS,
    TENANT_SCOPED_GRANT_TOOLS,
    TOOL_PERMISSIONS,
    ToolAccessNotAuthorizedError,
    authorize_tool,
    management_tools_for,
)

#: The guard's refusal reason for a tenant-scoped grant on a fleet-wide rule.
SCOPE_REFUSAL = "tenant-scoped grant; route requires a global grant"

#: The rules whose handlers confine what they serve or change to the grant's
#: tenant. Growing this set is a claim about a handler, so it is pinned here.
TENANT_AWARE_RULES = {
    "GET /mcp_servers/{id}/tools/history",
    "POST /admin/tools/{server}/{tool:path}/withdraw",
    "POST /admin/tools/{server}/{tool:path}/restore",
    "ANY /ws/events",
    "POST /approvals/{approval_id}/resolve",
    "GET /approvals/{approval_id}",
    "GET /approvals",
}


def _principal(name: str = "user:alice", tenant: str | None = "A", groups: frozenset[str] = frozenset()) -> Principal:
    return Principal(id=PrincipalId(name), type=PrincipalType.USER, tenant_id=tenant, groups=groups)


def _request(principal: Principal, resource_type: str = "mcp_servers", action: str = "read") -> AuthorizationRequest:
    return AuthorizationRequest(principal=principal, action=action, resource_type=resource_type, resource_id="*")


# ---------------------------------------------------------------------------
# The authorizer reports where the grant came from
# ---------------------------------------------------------------------------


class TestTheAuthorizerReportsTheScopeItGrantedAt:
    def test_a_global_binding_is_reported_global(self):
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="global")

        result = RBACAuthorizer(store).authorize(_request(_principal()))

        assert result.allowed and result.grant_scope == "global"

    def test_a_tenant_binding_is_reported_at_that_tenant(self):
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="tenant:A")

        result = RBACAuthorizer(store).authorize(_request(_principal()))

        assert result.allowed and result.grant_scope == "tenant:A"

    def test_a_group_tenant_binding_is_reported_at_that_tenant(self):
        store = InMemoryRoleStore()
        store.assign_role("group:ops", "viewer", scope="tenant:A")

        result = RBACAuthorizer(store).authorize(_request(_principal(groups=frozenset({"ops"}))))

        assert result.allowed and result.grant_scope == "tenant:A"

    def test_the_wider_grant_is_reported_when_both_exist(self):
        """Global first: holding a permission fleet-wide is not narrowed by also holding it in a tenant."""
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="tenant:A")
        store.assign_role("group:ops", "viewer", scope="global")

        result = RBACAuthorizer(store).authorize(_request(_principal(groups=frozenset({"ops"}))))

        assert result.allowed and result.grant_scope == "global"

    def test_a_tenant_binding_that_does_not_hold_the_permission_is_not_reported(self):
        """The first binding that actually holds the permission decides the scope."""
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="global")  # no lifecycle
        store.assign_role("user:alice", "developer", scope="tenant:A")  # lifecycle

        result = RBACAuthorizer(store).authorize(_request(_principal(), action="lifecycle"))

        assert result.allowed and result.grant_scope == "tenant:A"

    def test_another_tenants_binding_grants_nothing(self):
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="tenant:B")

        result = RBACAuthorizer(store).authorize(_request(_principal(tenant="A")))

        assert not result.allowed and result.grant_scope is None

    def test_the_system_principal_is_global(self):
        result = RBACAuthorizer(InMemoryRoleStore()).authorize(_request(Principal.system()))

        assert result.allowed and result.grant_scope == "global"

    def test_the_middleware_hands_the_decision_back(self):
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="tenant:A")

        result = AuthorizationMiddleware(RBACAuthorizer(store)).authorize(
            principal=_principal(), action="read", resource_type="mcp_servers", resource_id="*"
        )

        assert isinstance(result, AuthorizationResult)
        assert result.grant_scope == "tenant:A"


class TestAPolicyEngineDoesNotWidenTheScope:
    def _rbac(self) -> RBACAuthorizer:
        store = InMemoryRoleStore()
        store.assign_role("user:alice", "viewer", scope="tenant:A")
        return RBACAuthorizer(store)

    def test_require_both_keeps_the_scope_rbac_granted_at(self):
        opa = Mock()
        opa.authorize.return_value = AuthorizationResult.allow(reason="opa_allowed")
        combined = CombinedAuthorizer(rbac_authorizer=self._rbac(), opa_authorizer=opa, require_both=True)

        result = combined.authorize(_request(_principal()))

        assert result.allowed and result.grant_scope == "tenant:A"

    def test_rbac_first_passes_the_rbac_decision_through(self):
        combined = CombinedAuthorizer(rbac_authorizer=self._rbac(), opa_authorizer=Mock(), require_both=False)

        result = combined.authorize(_request(_principal()))

        assert result.allowed and result.grant_scope == "tenant:A"


class TestGrantScope:
    @pytest.mark.parametrize("scope", ["global", None])
    def test_a_global_or_unscoped_decision_reaches_the_fleet(self, scope):
        assert GrantScope.of(AuthorizationResult.allow(scope=scope)) == GrantScope(confined=False, tenant_id=None)

    @pytest.mark.parametrize("decision", [None, Mock(), "allowed"])
    def test_something_that_is_not_a_decision_carries_no_scope(self, decision):
        assert GrantScope.of(decision) == GrantScope()

    def test_a_tenant_scope_is_confined_to_that_tenant(self):
        assert GrantScope.of(AuthorizationResult.allow(scope="tenant:A")) == GrantScope(confined=True, tenant_id="A")

    def test_only_the_scope_prefix_is_stripped(self):
        """A tenant id may itself look like a scope; only the binding's prefix comes off."""
        assert GrantScope.of(AuthorizationResult.allow(scope="tenant:tenant:a")).tenant_id == "tenant:a"

    @pytest.mark.parametrize("scope", ["tenant:", "namespace:x", "*", "Global", ""])
    def test_an_unrecognised_scope_is_confined_to_no_tenant(self, scope):
        assert GrantScope.of(AuthorizationResult.allow(scope=scope)) == GrantScope(confined=True, tenant_id=None)


# ---------------------------------------------------------------------------
# Which tenant an event belongs to, and who receives it
# ---------------------------------------------------------------------------


def _invocation(tenant: str | None) -> ToolInvocationFailed:
    identity = {"user_id": "svc:x", "tenant_id": tenant} if tenant is not None else None
    return ToolInvocationFailed(mcp_server_id="srv", tool_name="t", identity_context=identity)


class TestWhichTenantAnEventBelongsTo:
    def test_the_caller_identity_names_it(self):
        assert event_tenant_id(_invocation("A")) == "A"

    def test_a_tenant_attribute_names_it(self):
        assert event_tenant_id(ToolWithdrawn(tenant_id="A", mcp_server="srv", tool="t", kind="tool")) == "A"

    def test_both_agreeing_name_it(self):
        assert event_tenant_id(SimpleNamespace(tenant_id="A", identity_context={"tenant_id": "A"})) == "A"

    def test_two_different_tenants_name_none(self):
        """Attributable to neither, so withheld from both."""
        assert event_tenant_id(SimpleNamespace(tenant_id="A", identity_context={"tenant_id": "B"})) is None

    @pytest.mark.parametrize(
        "event",
        [
            _invocation(None),
            ToolInvocationFailed(mcp_server_id="srv", identity_context={"user_id": "svc:x", "tenant_id": None}),
            ToolWithdrawn(tenant_id=None, mcp_server="srv", tool="t", kind="tool"),
            SimpleNamespace(tenant_id=""),
            SimpleNamespace(tenant_id=7),
            SimpleNamespace(identity_context="tenant_id=A"),
            object(),
        ],
        ids=["anonymous-call", "null-tenant", "all-tenants", "empty", "not-a-string", "not-a-mapping", "bare"],
    )
    def test_no_single_tenant_is_none(self, event):
        assert event_tenant_id(event) is None


class TestWhatAConfinedSubscriberReceives:
    def test_a_fleet_wide_subscriber_receives_everything(self):
        assert all(visible_to_tenant(_invocation(t), None) for t in ("A", "B", None))

    def test_a_confined_subscriber_receives_only_its_own_tenant(self):
        assert visible_to_tenant(_invocation("A"), "A")
        assert not visible_to_tenant(_invocation("B"), "A")
        assert not visible_to_tenant(_invocation(None), "A")


# ---------------------------------------------------------------------------
# The route table: which rules may pass a tenant-scoped grant
# ---------------------------------------------------------------------------


def _requests_for(rule: RouteRule) -> list[tuple[str, str, str]]:
    """``(asgi type, method, path)`` for every request the rule governs."""
    methods_part, template = rule.template.split(" ", 1)
    path = re.sub(r"\{[a-zA-Z_][a-zA-Z_0-9]*(:path)?\}", "x1", template).replace("/**", "/roles")
    if template == "/ws/events":
        return [("websocket", "GET", path)]
    methods = ["GET", "POST"] if methods_part == "ANY" else sorted(rule.methods or ())
    return [("http", method, path) for method in methods]


_GUARDED = [
    pytest.param(rule, asgi_type, method, path, id=f"{method} {path}")
    for rule in ROUTE_PERMISSIONS
    if rule.permission is not None
    for asgi_type, method, path in _requests_for(rule)
]


class TestWhichRoutesAreTenantAware:
    def test_the_tenant_aware_set_is_exactly_the_reviewed_one(self):
        """Adding a rule here is a claim that its handler confines by tenant.

        Change this set only together with that handler, and with a served-stack
        test in tests/integration/test_tenant_scoped_grants_on_served_app.py that
        proves the confinement.
        """
        assert {rule.template for rule in ROUTE_PERMISSIONS if rule.tenant_aware} == TENANT_AWARE_RULES

    def test_a_tenant_aware_rule_always_names_a_permission(self):
        """With no permission there is no grant, so no scope to confine by."""
        assert all(rule.permission is not None for rule in ROUTE_PERMISSIONS if rule.tenant_aware)

    def test_only_the_self_describing_routes_consult_no_grant(self):
        assert {rule.template for rule in ROUTE_PERMISSIONS if rule.permission is None} == {
            "GET /system/me",
            "GET /system",
        }

    @pytest.mark.parametrize(("rule", "asgi_type", "method", "path"), _GUARDED)
    def test_every_concrete_request_resolves_to_its_own_rule(self, rule, asgi_type, method, path):
        """Otherwise the guard tests below would be exercising some other rule."""
        assert resolve_rule(method, path) is rule


class _Unreadable:
    """An application context that cannot be read: proves a decision came from the guard."""

    def __getattr__(self, name):
        raise RuntimeError("context must not be consulted when the guard recorded a decision")


async def _drive(app, asgi_type: str, method: str, path: str, principal: Principal) -> list[dict]:
    scope = {
        "type": asgi_type,
        "method": method,
        "path": path,
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "state": {"auth": SimpleNamespace(principal=principal)},
    }
    sent: list[dict] = []

    async def receive():
        if asgi_type == "websocket":
            return {"type": "websocket.connect"}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _guard(permission: tuple[str, str], scope: str, authorizer=None):
    """The real route guard over a stub handler that reports the tenant it was confined to."""
    store = InMemoryRoleStore()
    store.add_role(
        Role(name="holds-it", permissions=frozenset({Permission(resource_type=permission[0], action=permission[1])}))
    )
    store.assign_role("user:alice", "holds-it", scope=scope)
    reached: dict[str, object] = {}

    async def handler(asgi_scope, receive, send):
        reached["tenant"] = confined_tenant(HTTPConnection(asgi_scope))
        if asgi_scope["type"] == "websocket":
            await send({"type": "websocket.accept"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    components = SimpleNamespace(
        enabled=True,
        authz_middleware=AuthorizationMiddleware(authorizer=authorizer or RBACAuthorizer(store)),
    )
    return AuthorizationEnforcementMiddleware(handler, auth_components=components), reached


def _refusal(sent: list[dict]) -> tuple[int, str | None]:
    """``(status or close code, reason)`` of a refusal."""
    if sent and sent[0]["type"] == "websocket.close":
        # A close frame carries the whole message: "Access denied: ... (<reason>)".
        message = sent[0].get("reason") or ""
        return sent[0]["code"], message[message.rfind("(") + 1 : -1] if message.endswith(")") else message
    status = sent[0]["status"]
    body = json.loads(b"".join(m.get("body", b"") for m in sent[1:]) or b"{}")
    return status, ((body.get("error") or {}).get("details") or {}).get("reason")


class TestTheGuardOverEveryRule:
    """Driven over the whole table, so a rule added later cannot skip the rule."""

    @pytest.fixture(autouse=True)
    def _context_is_not_consulted(self, monkeypatch):
        monkeypatch.setattr("mcp_hangar.server.api.tenant_scope.get_context", lambda: _Unreadable())

    @pytest.mark.parametrize(
        ("rule", "asgi_type", "method", "path"), [p for p in _GUARDED if not p.values[0].tenant_aware]
    )
    async def test_a_tenant_scoped_grant_is_refused_where_the_rule_is_not_tenant_aware(
        self, rule, asgi_type, method, path
    ):
        guard, reached = _guard(rule.permission, "tenant:A")

        sent = await _drive(guard, asgi_type, method, path, _principal())

        assert reached == {}, "the handler ran for a tenant-scoped grant on a fleet-wide rule"
        assert _refusal(sent) == (1008 if asgi_type == "websocket" else 403, SCOPE_REFUSAL)

    @pytest.mark.parametrize(("rule", "asgi_type", "method", "path"), [p for p in _GUARDED if p.values[0].tenant_aware])
    async def test_a_tenant_scoped_grant_passes_a_tenant_aware_rule_confined_to_its_tenant(
        self, rule, asgi_type, method, path
    ):
        guard, reached = _guard(rule.permission, "tenant:A")

        await _drive(guard, asgi_type, method, path, _principal())

        assert reached == {"tenant": "A"}

    @pytest.mark.parametrize(("rule", "asgi_type", "method", "path"), _GUARDED)
    async def test_a_global_grant_passes_every_rule_unconfined(self, rule, asgi_type, method, path):
        """The unchanged half: a global grant -- even for a principal that has a tenant -- reaches the fleet."""
        guard, reached = _guard(rule.permission, "global")

        await _drive(guard, asgi_type, method, path, _principal(tenant="A"))

        assert reached == {"tenant": None}

    @pytest.mark.parametrize(("rule", "asgi_type", "method", "path"), _GUARDED)
    async def test_no_grant_is_still_refused(self, rule, asgi_type, method, path):
        guard, reached = _guard(("nothing", "matches"), "global")

        sent = await _drive(guard, asgi_type, method, path, _principal())

        assert reached == {}
        code, reason = _refusal(sent)
        assert code == (1008 if asgi_type == "websocket" else 403)
        assert reason != SCOPE_REFUSAL

    @pytest.mark.parametrize(("rule", "asgi_type", "method", "path"), [p for p in _GUARDED if p.values[0].tenant_aware])
    async def test_an_unrecognised_scope_is_refused_even_where_tenants_are_served(self, rule, asgi_type, method, path):
        odd = Mock()
        odd.authorize.return_value = AuthorizationResult.allow(reason="custom", scope="namespace:x")
        guard, reached = _guard(rule.permission, "global", authorizer=odd)

        sent = await _drive(guard, asgi_type, method, path, _principal())

        assert reached == {}
        assert _refusal(sent) == (1008 if asgi_type == "websocket" else 403, SCOPE_REFUSAL)

    async def test_auth_off_records_an_unconfined_request(self):
        """Auth off narrows nothing, and the guard says so instead of leaving the handler to guess."""
        reached: dict[str, object] = {}

        async def handler(asgi_scope, receive, send):
            reached["tenant"] = confined_tenant(HTTPConnection(asgi_scope))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        guard = AuthorizationEnforcementMiddleware(handler, auth_components=None)
        await _drive(guard, "http", "GET", "/mcp_servers/x1/tools/history", _principal())

        assert reached == {"tenant": None}


# ---------------------------------------------------------------------------
# A handler reached without the guard's decision
# ---------------------------------------------------------------------------


def _conn(state: object | None = None) -> HTTPConnection:
    scope: dict[str, object] = {"type": "http", "path": "/x", "query_string": b"", "headers": []}
    if state is not None:
        scope["state"] = state
    return HTTPConnection(scope)


class TestAHandlerWithoutTheGuardsDecision:
    def test_auth_off_reads_as_unconfined(self, monkeypatch):
        monkeypatch.setattr(
            "mcp_hangar.server.api.tenant_scope.get_context", lambda: SimpleNamespace(auth_components=None)
        )
        assert confined_tenant(_conn()) is None

    def test_disabled_auth_components_read_as_unconfined(self, monkeypatch):
        components = SimpleNamespace(enabled=False, authz_middleware=object())
        monkeypatch.setattr(
            "mcp_hangar.server.api.tenant_scope.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        assert confined_tenant(_conn()) is None

    def test_auth_on_without_a_decision_is_refused(self, monkeypatch):
        """A missing decision is not a global one."""
        components = SimpleNamespace(enabled=True, authz_middleware=object())
        monkeypatch.setattr(
            "mcp_hangar.server.api.tenant_scope.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        with pytest.raises(AccessDeniedError, match="no authorization decision recorded"):
            confined_tenant(_conn())

    def test_an_unreadable_context_is_refused(self, monkeypatch):
        def broken():
            raise RuntimeError("no context")

        monkeypatch.setattr("mcp_hangar.server.api.tenant_scope.get_context", broken)
        with pytest.raises(AccessDeniedError, match="no authorization decision recorded"):
            confined_tenant(_conn())

    def test_a_recorded_scope_nothing_recognises_is_refused(self):
        with pytest.raises(AccessDeniedError, match="unrecognised grant scope"):
            confined_tenant(_conn({"authz_grant_scope": GrantScope(confined=True, tenant_id=None)}))

    def test_a_decision_is_recorded_on_either_shape_of_state(self):
        """The outer auth middleware installs a State; Starlette's Request.state wraps a dict."""
        as_dict: dict[str, object] = {"type": "http"}
        record_grant_scope(as_dict, GrantScope(confined=True, tenant_id="A"))
        as_state: dict[str, object] = {"type": "http", "state": State()}
        record_grant_scope(as_state, GrantScope(confined=True, tenant_id="B"))

        assert confined_tenant(_conn(as_dict["state"])) == "A"
        assert confined_tenant(_conn(as_state["state"])) == "B"

    async def test_the_event_stream_does_not_open_without_a_decision(self, monkeypatch):
        """The socket's own fallback: auth on, no guard decision -> 1008 before accept."""
        from unittest.mock import AsyncMock, MagicMock

        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        components = SimpleNamespace(enabled=True, authz_middleware=object())
        monkeypatch.setattr(
            "mcp_hangar.server.api.tenant_scope.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        ws = MagicMock()
        ws.headers = {}
        ws.scope = {"type": "websocket", "path": "/ws/events", "query_string": b"", "headers": []}
        ws.url = MagicMock(path="/ws/events")
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        await ws_events_endpoint(ws)

        ws.accept.assert_not_awaited()
        ws.close.assert_awaited_once_with(code=1008, reason="no authorization decision recorded")


# ---------------------------------------------------------------------------
# The MCP management-tool surface
# ---------------------------------------------------------------------------

_MANAGEMENT_TOOLS = sorted(set(TOOL_PERMISSIONS) - TENANT_SCOPED_GRANT_TOOLS - SELF_AUTHORIZING_TOOLS)


def _mcp_ctx(principal: Principal | None) -> SimpleNamespace:
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth))))


@pytest.fixture()
def granted(monkeypatch):
    """Install enabled auth where ``user:alice`` (tenant A) holds *role* at *scope*."""

    def install(role: str, scope: str) -> Principal:
        principal = _principal()
        store = InMemoryRoleStore()
        store.assign_role("user:alice", role, scope=scope)
        components = SimpleNamespace(enabled=True, authz_middleware=AuthorizationMiddleware(RBACAuthorizer(store)))
        monkeypatch.setattr(
            "mcp_hangar.server.context.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        return principal

    return install


class TestTheManagementToolsNeedAGlobalGrant:
    def test_the_tenant_scoped_tools_are_the_invoke_path_and_nothing_else(self):
        assert {"hangar_fetch_continuation", "hangar_delete_continuation"} == TENANT_SCOPED_GRANT_TOOLS
        assert TENANT_SCOPED_GRANT_TOOLS <= INVOKE_PATH_TOOLS

    @pytest.mark.parametrize("tool", _MANAGEMENT_TOOLS)
    def test_a_tenant_scoped_admin_is_refused_every_management_tool(self, granted, tool):
        principal = granted("admin", "tenant:A")

        with pytest.raises(ToolAccessNotAuthorizedError, match="global role grant is required"):
            authorize_tool(tool, _mcp_ctx(principal))

    @pytest.mark.parametrize("tool", _MANAGEMENT_TOOLS)
    def test_a_global_admin_is_allowed_every_management_tool(self, granted, tool):
        authorize_tool(tool, _mcp_ctx(granted("admin", "global")))

    @pytest.mark.parametrize("tool", sorted(TENANT_SCOPED_GRANT_TOOLS))
    def test_a_tenant_scoped_invoker_keeps_its_continuations(self, granted, tool):
        authorize_tool(tool, _mcp_ctx(granted("developer", "tenant:A")))

    def test_a_tenant_scoped_admin_is_shown_no_management_tool(self, granted):
        assert management_tools_for(_mcp_ctx(granted("admin", "tenant:A"))) == frozenset()

    def test_a_global_admin_is_shown_all_of_them(self, granted):
        assert (
            management_tools_for(_mcp_ctx(granted("admin", "global")))
            == frozenset(TOOL_PERMISSIONS) - INVOKE_PATH_TOOLS
        )


class TestTheFrontDoorServesATenantGrantNoControlPlane:
    """Driving the installed handlers `mcp-hangar serve` builds, not the helpers."""

    @pytest.fixture(autouse=True)
    def _singletons(self):
        reset_tool_projection_registry()
        reset_tool_access_resolver()
        yield
        reset_tool_projection_registry()
        reset_tool_access_resolver()

    def _front_door(self, granted, role: str, scope: str):
        from mcp_hangar._sdk_compat import lowlevel_server
        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        get_tool_access_resolver().set_topology_mode("front_door")
        server = build_serving_mcp_server()
        get_tool_projection_registry().build_from_tools(
            "payments", [ToolSchema(name="refund", description="Refund a payment", input_schema={"type": "object"})]
        )
        principal = granted(role, scope)
        low = lowlevel_server(server)
        request_ctx = SimpleNamespace(
            request=SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=principal)))
        )
        return low.get_request_handler("tools/list").handler, low.get_request_handler("tools/call").handler, request_ctx

    async def test_a_tenant_scoped_operator_sees_and_calls_no_management_tool(self, granted):
        list_tools, call_tool, request_ctx = self._front_door(granted, "provider-admin", "tenant:A")

        names = {tool.name for tool in (await list_tools(request_ctx, None)).tools}
        assert "refund" in names
        assert not any(name.startswith("hangar_") for name in names)

        with pytest.raises(Exception) as excinfo:
            await call_tool(request_ctx, SimpleNamespace(name="hangar_list", arguments={}))
        assert "not found" in str(excinfo.value).lower()

    async def test_a_global_operator_still_gets_the_control_plane(self, granted):
        list_tools, _call_tool, request_ctx = self._front_door(granted, "provider-admin", "global")

        names = {tool.name for tool in (await list_tools(request_ctx, None)).tools}
        assert {"refund", "hangar_list", "hangar_details"} <= names


# ---------------------------------------------------------------------------
# Resolving an approval: enforced in the command handler, whatever the transport
# ---------------------------------------------------------------------------


def _approval(approval_id: str, tenant: str | None):
    from datetime import UTC, datetime, timedelta

    from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState

    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id=approval_id,
        mcp_server_id="srv",
        tool_name="t",
        arguments={},
        arguments_hash="sha256:x",
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
        state=ApprovalState.PENDING,
        channel="noop",
        tenant_id=tenant,
    )


class TestTheResolveHandlerConfinesATenantGrant:
    """The REST route is one transport; the handler is the chokepoint every transport shares."""

    def _handler(self, scope: str, authorizer=None):
        from unittest.mock import AsyncMock

        from mcp_hangar.approvals.commands.resolve import ResolveApprovalHandler

        store = InMemoryRoleStore()
        store.add_role(Role(name="approver", permissions=frozenset({Permission("approval", "resolve")})))
        store.assign_role("user:alice", "approver", scope=scope)
        approvals = {a.approval_id: a for a in (_approval("fleet", None), _approval("a", "A"), _approval("b", "B"))}

        async def get(approval_id):
            return approvals.get(approval_id)

        service = SimpleNamespace(_repository=SimpleNamespace(get=get), resolve=AsyncMock(return_value=True))
        components = SimpleNamespace(
            enabled=True, authz_middleware=AuthorizationMiddleware(authorizer or RBACAuthorizer(store))
        )
        return ResolveApprovalHandler(service, auth_components=components), service

    async def _outcome(self, handler, approval_id: str):
        from mcp_hangar.approvals.commands.resolve import ResolveApprovalCommand

        result = await handler.handle(
            ResolveApprovalCommand(approval_id=approval_id, approved=True, principal=_principal())
        )
        return result.outcome.value

    @pytest.mark.parametrize(
        ("approval_id", "expected"), [("fleet", "not_found"), ("b", "not_found"), ("a", "resolved")]
    )
    async def test_a_tenant_scoped_grant_resolves_only_its_own_tenant(self, approval_id, expected):
        handler, service = self._handler("tenant:A")

        assert await self._outcome(handler, approval_id) == expected
        assert service.resolve.await_count == (1 if expected == "resolved" else 0)

    @pytest.mark.parametrize(
        ("approval_id", "expected"), [("fleet", "resolved"), ("a", "resolved"), ("b", "not_found")]
    )
    async def test_a_global_grant_is_unchanged(self, approval_id, expected):
        """Tenant B stays out of reach for a caller in tenant A, as before: the caller's tenant, not the grant."""
        handler, _service = self._handler("global")

        assert await self._outcome(handler, approval_id) == expected

    async def test_a_scope_nothing_recognises_matches_no_approval(self):
        """Not even a tenantless one: an unrecognised grant confines to no tenant, and None is not a tenant."""
        odd = Mock()
        odd.authorize.return_value = AuthorizationResult.allow(reason="custom", scope="namespace:x")
        handler, service = self._handler("global", authorizer=odd)

        assert await self._outcome(handler, "fleet") == "not_found"
        service.resolve.assert_not_awaited()
