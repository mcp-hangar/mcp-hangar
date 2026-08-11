"""Every tool on the MCP control-plane surface authorizes, and none is forgotten (#909).

`hangar_call` authorized every call it dispatched; the other twenty-one
`hangar_*` tools authorized nothing, so with auth on a `viewer` was refused
`POST /api/mcp_servers/{id}/stop` and accepted on `hangar_stop` -- same identity,
same process, one door guarded.

Two things are pinned here, and the second is the one that keeps this fixed:

1. the guard denies and permits the right callers;
2. the guard is *reachable* -- the hook is installed by `register_all_tools`,
   and every tool that function registers is named in the table. A tool added
   without an entry fails this file rather than shipping open, because "the
   author remembers" is the assumption that produced the issue.

The surface is read off the real server built by `build_serving_mcp_server`,
which is the one `mcp-hangar serve` exposes -- not a list rewritten here that
could agree with the table while disagreeing with production.
"""

from types import SimpleNamespace

import pytest

from mcp_hangar.application.mcp.tooling import get_tool_authorizer, set_tool_authorizer
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.tools.tool_permissions import (
    SELF_AUTHORIZING_TOOLS,
    TOOL_PERMISSIONS,
    ToolAccessNotAuthorizedError,
    authorize_tool,
)


def _principal(name: str = "user:alice") -> Principal:
    return Principal(id=PrincipalId(name), type=PrincipalType.USER)


def _ctx(principal: Principal | None) -> SimpleNamespace:
    """The MCP request Context shape the wrapper injects."""
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth))))


def _components(principal: Principal, role: str | None, *, enabled: bool = True) -> SimpleNamespace:
    store = InMemoryRoleStore()
    if role is not None:
        store.assign_role(principal_id=str(principal.id), role_name=role)
    return SimpleNamespace(
        enabled=enabled,
        authz_middleware=AuthorizationMiddleware(authorizer=RBACAuthorizer(store)),
    )


@pytest.fixture()
def with_auth(monkeypatch):
    """Install auth components the guard will resolve through the app context."""

    def install(role: str | None, *, enabled: bool = True, principal: Principal | None = None):
        p = principal if principal is not None else _principal()
        components = _components(p, role, enabled=enabled)
        monkeypatch.setattr(
            "mcp_hangar.server.context.get_context",
            lambda: SimpleNamespace(auth_components=components),
        )
        return p

    return install


class TestTheGuardDecides:
    def test_a_viewer_cannot_stop_a_server(self, with_auth):
        """The finding: REST refuses this identity, MCP accepted it."""
        principal = with_auth("viewer")

        with pytest.raises(ToolAccessNotAuthorizedError) as excinfo:
            authorize_tool("hangar_stop", _ctx(principal))

        assert "mcp_servers:lifecycle" in str(excinfo.value)

    def test_a_viewer_may_still_read_the_fleet(self, with_auth):
        """The guard must not collapse into deny-all: viewer holds mcp_servers:read."""
        principal = with_auth("viewer")

        authorize_tool("hangar_list", _ctx(principal))

    def test_an_admin_may_stop_a_server(self, with_auth):
        principal = with_auth("admin")

        authorize_tool("hangar_stop", _ctx(principal))

    def test_a_developer_cannot_reload_the_configuration(self, with_auth):
        """Reload re-applies every governance input; the REST route is admin-only."""
        principal = with_auth("developer")

        with pytest.raises(ToolAccessNotAuthorizedError):
            authorize_tool("hangar_reload_config", _ctx(principal))

    def test_an_anonymous_caller_is_refused_under_configured_auth(self, with_auth):
        with_auth("admin")

        with pytest.raises(ToolAccessNotAuthorizedError):
            authorize_tool("hangar_stop", _ctx(Principal.anonymous()))

    def test_a_missing_principal_is_refused_under_configured_auth(self, with_auth):
        with_auth("admin")

        with pytest.raises(ToolAccessNotAuthorizedError):
            authorize_tool("hangar_stop", _ctx(None))

    def test_an_unmapped_tool_is_refused_rather_than_public(self, with_auth):
        """The fail-closed default. A forgotten entry must not be an open door."""
        principal = with_auth("admin")

        with pytest.raises(ToolAccessNotAuthorizedError) as excinfo:
            authorize_tool("hangar_a_tool_nobody_mapped", _ctx(principal))

        assert "TOOL_PERMISSIONS" in str(excinfo.value)

    def test_hangar_call_is_left_to_its_own_per_call_check(self, with_auth):
        """A coarser second rule in front of `_authorize_calls` could only drift."""
        principal = with_auth("viewer")

        authorize_tool("hangar_call", _ctx(principal))


class TestAuthOffStaysUsable:
    def test_disabled_auth_components_allow_everything(self, with_auth):
        """`--unsafe-no-auth` must stay usable; this is the #600 regression."""
        principal = with_auth("viewer", enabled=False)

        authorize_tool("hangar_stop", _ctx(principal))

    def test_no_application_context_allows_everything(self, monkeypatch):
        """stdio and local runs resolve no context at all."""

        def boom():
            raise RuntimeError("no application context")

        monkeypatch.setattr("mcp_hangar.server.context.get_context", boom)

        authorize_tool("hangar_stop", _ctx(_principal()))


class TestTheWrapperCallsTheHook:
    """Installed is not called. This is the edge the other tests assume."""

    def test_a_refusal_stops_the_tool_body(self):
        from mcp_hangar.application.mcp.tooling import mcp_tool_wrapper

        ran = []

        def refuse(_tool_name, _ctx):
            raise ToolAccessNotAuthorizedError("no")

        @mcp_tool_wrapper(
            tool_name="hangar_stop",
            rate_limit_key=lambda *_a, **_k: "k",
            check_rate_limit=lambda _k: None,
        )
        def tool_body(mcp_server: str) -> dict:
            ran.append(mcp_server)
            return {"stopped": mcp_server}

        set_tool_authorizer(refuse)
        try:
            with pytest.raises(ToolAccessNotAuthorizedError):
                tool_body("math")
        finally:
            set_tool_authorizer(authorize_tool)

        assert ran == [], "the tool body ran despite the refusal"

    def test_the_hook_receives_the_tool_name_and_the_request_context(self):
        from mcp_hangar.application.mcp.tooling import _CTX_KW, mcp_tool_wrapper

        seen = []

        @mcp_tool_wrapper(
            tool_name="hangar_stop",
            rate_limit_key=lambda *_a, **_k: "k",
            check_rate_limit=lambda _k: None,
        )
        def tool_body(mcp_server: str) -> dict:
            return {"stopped": mcp_server}

        ctx = _ctx(_principal())
        set_tool_authorizer(lambda name, c: seen.append((name, c)))
        try:
            tool_body("math", **{_CTX_KW: ctx})
        finally:
            set_tool_authorizer(authorize_tool)

        assert seen == [("hangar_stop", ctx)]

    def test_authorization_precedes_the_approval_gate(self):
        """An unauthorized caller must not be able to summon a human decision."""
        import anyio

        from mcp_hangar.application.mcp.tooling import mcp_tool_wrapper

        asked = []

        async def check_approval(*_a, **_k):
            asked.append(True)
            raise AssertionError("approval gate reached by an unauthorized caller")

        @mcp_tool_wrapper(
            tool_name="hangar_stop",
            rate_limit_key=lambda *_a, **_k: "k",
            check_rate_limit=lambda _k: None,
            check_approval=check_approval,
        )
        async def tool_body(mcp_server: str) -> dict:
            return {"stopped": mcp_server}

        def refuse(_tool_name, _ctx):
            raise ToolAccessNotAuthorizedError("no")

        set_tool_authorizer(refuse)
        try:
            with pytest.raises(ToolAccessNotAuthorizedError):
                anyio.run(tool_body, "math")
        finally:
            set_tool_authorizer(authorize_tool)

        assert asked == []


class TestTheGuardIsReachable:
    """The half that keeps this fixed. A guard nothing calls is not a guard."""

    def test_register_all_tools_installs_the_hook(self, monkeypatch):
        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        set_tool_authorizer(None)
        try:
            build_serving_mcp_server()
            assert get_tool_authorizer() is authorize_tool
        finally:
            set_tool_authorizer(authorize_tool)

    @pytest.mark.anyio
    async def test_every_registered_tool_is_named_in_the_table(self):
        """Read off the served surface, not off a list maintained beside it."""
        from mcp_hangar._sdk_compat import lowlevel_server  # noqa: F401 -- import parity with production
        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        server = build_serving_mcp_server()
        registered = {tool.name for tool in await _list_tools(server)}

        assert registered, "the served surface registered no tools; the probe is broken, not the table"

        unmapped = registered - set(TOOL_PERMISSIONS) - SELF_AUTHORIZING_TOOLS
        assert not unmapped, (
            f"tools registered with no entry in TOOL_PERMISSIONS: {sorted(unmapped)}. "
            "An unmapped tool is refused at runtime, so this is a defect in the tool, not in the table."
        )

    @pytest.mark.anyio
    async def test_every_registered_tool_actually_passes_through_the_wrapper(self):
        """Being named in the table is not the same as being guarded.

        Both continuation tools were registered without `mcp_tool_wrapper`, so a
        table-only check would have called them covered while every call went
        straight to the body.
        """
        from mcp_hangar.application.mcp.tooling import GUARDED_TOOL_ATTR
        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        server = build_serving_mcp_server()
        unguarded = []
        for tool in await _list_tools(server):
            if tool.name in SELF_AUTHORIZING_TOOLS:
                continue
            fn = _tool_callable(server, tool.name)
            if getattr(fn, GUARDED_TOOL_ATTR, None) != tool.name:
                unguarded.append(tool.name)

        assert not unguarded, (
            f"tools registered without mcp_tool_wrapper, so no authorization runs for them: {sorted(unguarded)}"
        )

    def test_the_table_names_no_tool_that_is_not_registered(self):
        """Keeps the table from accumulating entries for tools that were removed."""
        import anyio

        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        server = build_serving_mcp_server()
        registered = {tool.name for tool in anyio.run(_list_tools, server)}

        stale = set(TOOL_PERMISSIONS) - registered
        assert not stale, f"TOOL_PERMISSIONS names tools that are not registered: {sorted(stale)}"


async def _list_tools(server):
    result = server.list_tools()
    if hasattr(result, "__await__"):
        return await result
    return result


def _tool_callable(server, name: str):
    """The function the SDK will actually invoke for *name*."""
    manager = server._tool_manager
    return manager.get_tool(name).fn
