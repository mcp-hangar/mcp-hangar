"""Over stdio with auth on, the declared principal may call what it is listed (#1627).

A pipe carries no request, so ADR-026's ``auth.stdio.principal`` names the
caller. The front door already listed the ``hangar_*`` tools that principal's
declared roles permit, and ``authorize_tool`` read the principal only from the
request, so with ``auth.enabled: true`` it refused every one of them as
unauthenticated: shown, then refused.

``authorize_tool`` now resolves the principal as ``hangar_call`` does
(``_request_principal``) and decides the declared one with
``get_stdio_authorizer()``. What is pinned here:

1. listing and calling agree, for every built-in role and every management tool;
2. a declared ``admin`` calls what it is listed, a declared ``viewer`` is refused
   what it is not;
3. HTTP is unchanged, and a request never borrows the declared principal, on
   the listing or on the call.
"""

from types import SimpleNamespace

import pytest

from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.auth.roles import BUILTIN_ROLES
from mcp_hangar.auth.stdio_principal import clear_stdio_principal, get_stdio_principal
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.bootstrap import _declare_stdio_principal
from mcp_hangar.server.tools.tool_permissions import (
    INVOKE_PATH_TOOLS,
    TOOL_PERMISSIONS,
    ToolAccessNotAuthorizedError,
    authorize_tool,
    management_tools_for,
)

MANAGEMENT_TOOLS = sorted(set(TOOL_PERMISSIONS) - INVOKE_PATH_TOOLS)

#: The context a stdio session hands a tool: a request context with no request.
STDIO_CTX = SimpleNamespace(request_context=SimpleNamespace(request=None))


def _http_ctx(principal: Principal | None) -> SimpleNamespace:
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth))))


def _declare(roles: list[str]) -> Principal:
    _declare_stdio_principal(
        {"auth": {"stdio": {"principal": {"id": "local-user", "tenant_id": "local", "roles": roles}}}},
        stdio=True,
    )
    principal = get_stdio_principal()
    assert principal is not None
    return principal


def _allowed(tool: str, ctx: object) -> bool:
    try:
        authorize_tool(tool, ctx)
    except ToolAccessNotAuthorizedError:
        return False
    return True


@pytest.fixture(autouse=True)
def _no_leaked_principal():
    clear_stdio_principal()
    yield
    clear_stdio_principal()


@pytest.fixture()
def configured_store(monkeypatch) -> InMemoryRoleStore:
    """Auth on, with the configured (HTTP) role store the declaration never writes."""
    store = InMemoryRoleStore()
    components = SimpleNamespace(enabled=True, authz_middleware=AuthorizationMiddleware(RBACAuthorizer(store)))
    monkeypatch.setattr("mcp_hangar.server.context.get_context", lambda: SimpleNamespace(auth_components=components))
    return store


class TestListingAndCallingAgree:
    @pytest.mark.parametrize("roles", [[name] for name in sorted(BUILTIN_ROLES)] + [[], ["wizard"]], ids=str)
    def test_a_tool_is_listed_exactly_when_it_may_be_called(self, configured_store, roles):
        _declare(roles)

        listed = management_tools_for(STDIO_CTX)
        callable_ = {tool for tool in MANAGEMENT_TOOLS if _allowed(tool, STDIO_CTX)}

        assert listed == callable_

    def test_the_continuations_are_not_listed_and_follow_tool_invoke(self, configured_store):
        # Invoke-path plumbing, not management: never listed, callable with tool:invoke.
        _declare(["developer"])
        assert _allowed("hangar_fetch_continuation", STDIO_CTX)

        clear_stdio_principal()
        _declare(["viewer"])
        assert not _allowed("hangar_fetch_continuation", STDIO_CTX)
        assert "hangar_fetch_continuation" not in management_tools_for(STDIO_CTX)


class TestTheDeclaredRolesDecide:
    def test_a_declared_admin_calls_what_it_is_listed(self, configured_store):
        _declare(["admin"])

        listed = management_tools_for(STDIO_CTX)

        assert {"hangar_status", "hangar_stop", "hangar_reload_config"} <= listed
        for tool in listed:
            authorize_tool(tool, STDIO_CTX)

    def test_a_declared_viewer_is_refused_what_it_is_not_listed(self, configured_store):
        _declare(["viewer"])

        listed = management_tools_for(STDIO_CTX)

        assert "hangar_status" in listed
        authorize_tool("hangar_status", STDIO_CTX)
        for tool in ("hangar_stop", "hangar_start", "hangar_load", "hangar_reload_config"):
            assert tool not in listed
            with pytest.raises(ToolAccessNotAuthorizedError, match="permission required"):
                authorize_tool(tool, STDIO_CTX)

    def test_auth_off_still_allows_everything(self, monkeypatch):
        components = SimpleNamespace(enabled=False, authz_middleware=object())
        monkeypatch.setattr(
            "mcp_hangar.server.context.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        _declare(["viewer"])

        authorize_tool("hangar_stop", STDIO_CTX)


class TestHttpIsUnchanged:
    def test_a_request_with_no_principal_is_refused_beside_a_declared_admin(self, configured_store):
        _declare(["admin"])

        assert management_tools_for(_http_ctx(None)) == frozenset()
        with pytest.raises(ToolAccessNotAuthorizedError, match="Authentication required"):
            authorize_tool("hangar_status", _http_ctx(None))

    def test_a_request_with_the_declared_id_does_not_get_the_declared_roles(self, configured_store):
        _declare(["admin"])
        same_id = Principal(id=PrincipalId("local-user"), type=PrincipalType.USER, tenant_id="local")

        assert management_tools_for(_http_ctx(same_id)) == frozenset()
        with pytest.raises(ToolAccessNotAuthorizedError, match="permission required"):
            authorize_tool("hangar_status", _http_ctx(same_id))

    def test_a_request_principal_is_decided_by_the_configured_store(self, configured_store):
        _declare(["viewer"])
        operator = Principal(id=PrincipalId("user:ops"), type=PrincipalType.USER)
        configured_store.assign_role(principal_id="user:ops", role_name="admin")

        authorize_tool("hangar_stop", _http_ctx(operator))
        assert "hangar_stop" in management_tools_for(_http_ctx(operator))
