"""`hangar_whoami` reports the identity the authorizer decides on, and only the caller's own (#1363).

The property that matters is that the view cannot drift from the enforcement:
whatever roles it reports, a call those roles do not admit is refused, and one
they do admit is not. That is checked across the whole table rather than for
one hand-picked tool.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.opa_authorizer import CombinedAuthorizer
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.auth.stdio_principal import clear_stdio_principal
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.tools.tool_permissions import (
    TENANT_SCOPED_GRANT_TOOLS,
    TOOL_PERMISSIONS,
    ToolAccessNotAuthorizedError,
    authorize_tool,
    management_tools_for,
)
from mcp_hangar.server.tools.whoami import hangar_whoami


def _ctx(principal: Principal | None) -> SimpleNamespace:
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth))))


def _principal(tenant: str | None = None, groups: frozenset[str] = frozenset()) -> Principal:
    return Principal(id=PrincipalId("svc:ci"), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=tenant, groups=groups)


@pytest.fixture(autouse=True)
def _no_declared_principal():
    clear_stdio_principal()
    yield
    clear_stdio_principal()


@pytest.fixture()
def auth_on(monkeypatch):
    """Auth on over an RBAC store the test fills; returns the store."""

    def install(*, enabled: bool = True, authorizer=None) -> InMemoryRoleStore:
        store = InMemoryRoleStore()
        components = SimpleNamespace(
            enabled=enabled, authz_middleware=AuthorizationMiddleware(authorizer or RBACAuthorizer(store))
        )
        monkeypatch.setattr(
            "mcp_hangar.server.context.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        return store

    return install


def _allowed(tool: str, ctx: object) -> bool:
    try:
        authorize_tool(tool, ctx)
    except ToolAccessNotAuthorizedError:
        return False
    return True


def _roles_admit(report: dict, store: InMemoryRoleStore, tool: str) -> bool:
    """What the reported roles say about *tool*, read only from the report."""
    resource_type, action = TOOL_PERMISSIONS[tool]
    for binding in report["roles"]:
        role = store.get_role(binding["role"])
        if role is None or not role.has_permission(resource_type, action, "*"):
            continue
        if binding["scope"] == "global" or tool in TENANT_SCOPED_GRANT_TOOLS:
            return True
    return False


class TestTheReport:
    def test_it_names_the_principal_tenant_groups_and_every_binding(self, auth_on):
        store = auth_on()
        principal = _principal(tenant="A", groups=frozenset({"ops", "eng"}))
        store.assign_role("svc:ci", "viewer")
        store.assign_role("svc:ci", "developer", scope="tenant:A")
        store.assign_role("group:ops", "auditor")
        store.assign_role("group:eng", "viewer")  # also held directly: reported once

        report = hangar_whoami(_ctx(principal))

        assert report == {
            "principal": {"id": "svc:ci", "type": "service_account"},
            "tenant_id": "A",
            "groups": ["eng", "ops"],
            "roles": [
                {"role": "viewer", "scope": "global"},
                {"role": "auditor", "scope": "global"},
                {"role": "developer", "scope": "tenant:A"},
            ],
            "opa_policy": None,
        }

    def test_a_global_binding_subsumes_the_same_role_within_a_tenant(self, auth_on):
        """The SQL stores answer a tenant-scope lookup with global rows too; that is one binding."""
        store = auth_on()
        store.assign_role("svc:ci", "viewer")
        store.assign_role("svc:ci", "viewer", scope="tenant:A")

        assert hangar_whoami(_ctx(_principal(tenant="A")))["roles"] == [{"role": "viewer", "scope": "global"}]

    def test_a_caller_with_no_role_is_answered_with_none(self, auth_on):
        auth_on()

        assert hangar_whoami(_ctx(_principal()))["roles"] == []

    def test_an_opa_fallback_is_disclosed(self, auth_on):
        store = InMemoryRoleStore()
        auth_on(authorizer=CombinedAuthorizer(rbac_authorizer=RBACAuthorizer(store), opa_authorizer=Mock()))

        assert hangar_whoami(_ctx(_principal()))["opa_policy"] == "may_admit_more"

    def test_an_authorizer_with_no_visible_roles_fails_closed(self, auth_on):
        auth_on(authorizer=Mock(spec=["authorize"]))

        with pytest.raises(RuntimeError, match="does not expose role bindings"):
            hangar_whoami(_ctx(_principal()))


class TestTheViewAndTheEnforcementAgree:
    @pytest.mark.parametrize(
        "bindings",
        [
            [],
            [("viewer", "global")],
            [("developer", "tenant:A")],
            [("admin", "tenant:A")],
            [("provider-admin", "global"), ("developer", "tenant:A")],
            [("admin", "global")],
        ],
        ids=str,
    )
    def test_every_tool_is_refused_exactly_when_the_reported_roles_say_so(self, auth_on, bindings):
        store = auth_on()
        for role, scope in bindings:
            store.assign_role("svc:ci", role, scope=scope)
        ctx = _ctx(_principal(tenant="A"))

        report = hangar_whoami(ctx)

        for tool in TOOL_PERMISSIONS:
            assert _allowed(tool, ctx) == _roles_admit(report, store, tool), tool

    def test_a_call_the_reported_viewer_role_denies_is_denied(self, auth_on):
        store = auth_on()
        store.assign_role("svc:ci", "viewer")
        ctx = _ctx(_principal())

        report = hangar_whoami(ctx)
        assert not _roles_admit(report, store, "hangar_stop")

        with pytest.raises(ToolAccessNotAuthorizedError, match="mcp_servers:lifecycle"):
            authorize_tool("hangar_stop", ctx)


class TestWithNoIdentity:
    def test_auth_off_neither_shows_nor_answers_it(self, auth_on):
        auth_on(enabled=False)
        ctx = _ctx(_principal())

        assert management_tools_for(ctx) == frozenset()
        with pytest.raises(ToolAccessNotAuthorizedError):
            authorize_tool("hangar_whoami", ctx)
        with pytest.raises(ToolAccessNotAuthorizedError):
            hangar_whoami(ctx)

    def test_an_anonymous_caller_neither_sees_nor_calls_it(self, auth_on):
        auth_on()
        ctx = _ctx(Principal.anonymous())

        assert "hangar_whoami" not in management_tools_for(ctx)
        with pytest.raises(ToolAccessNotAuthorizedError):
            hangar_whoami(ctx)


class TestADeclaredStdioPrincipal:
    def test_it_reports_the_declared_roles_the_stdio_authorizer_decides_on(self):
        from mcp_hangar.server.bootstrap import _declare_stdio_principal

        _declare_stdio_principal(
            {"auth": {"stdio": {"principal": {"id": "local-user", "tenant_id": "local", "roles": ["viewer"]}}}},
            stdio=True,
        )
        stdio_ctx = SimpleNamespace(request_context=SimpleNamespace(request=None))

        report = hangar_whoami(stdio_ctx)

        assert report["principal"]["id"] == "local-user"
        assert report["tenant_id"] == "local"
        assert report["roles"] == [{"role": "viewer", "scope": "global"}]
        assert "hangar_whoami" in management_tools_for(stdio_ctx)


class TestTheServedTool:
    """Through the registered surface and the wrapper, not the helper."""

    @staticmethod
    def _served():
        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        server = build_serving_mcp_server()
        return server._tool_manager.get_tool("hangar_whoami")

    def test_it_takes_no_parameter(self):
        """So no argument can ask about another principal or tenant."""
        import anyio

        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        async def listed():
            result = build_serving_mcp_server().list_tools()
            return await result if hasattr(result, "__await__") else result

        [tool] = [tool for tool in anyio.run(listed) if tool.name == "hangar_whoami"]
        assert tool.input_schema.get("properties") == {}

    def test_the_wrapper_authorizes_against_the_tools_own_context(self, auth_on):
        """The body reads the caller from its own Context, so the guard must see that one."""
        auth_on()
        ctx = _ctx(_principal(tenant="A"))

        report = self._served().fn(ctx=ctx)

        assert report["principal"]["id"] == "svc:ci"

    def test_the_wrapper_refuses_an_anonymous_context(self, auth_on):
        auth_on()

        with pytest.raises(ToolAccessNotAuthorizedError):
            self._served().fn(ctx=_ctx(Principal.anonymous()))
