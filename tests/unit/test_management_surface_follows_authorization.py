"""One front door, an agent without a control plane and an operator with one (#904).

`front_door` swapped the whole surface at bootstrap: flat upstream names for
everyone, `hangar_*` for nobody. So an operator on a front-door gateway had no
management surface over MCP at all, and turning the mode off to get one handed
every agent the entire meta-API. Satisfying both meant two instances, which is
the criterion this is measured against.

The surface is now a property of the caller: a management tool appears exactly
when the caller is authorized to call it (ADR-022). These tests drive the real
front-door handlers, so what they check is the projection production serves.
"""

from types import SimpleNamespace

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.security import Permission, Principal, PrincipalId, PrincipalType, Role
from mcp_hangar.server.tools.tool_permissions import management_tools_for

_SERVER = "payments"
_UPSTREAM_TOOL = "refund"

#: What an agent principal actually looks like. None of the built-in roles fit:
#: `developer` holds mcp_servers read, write AND lifecycle, so an agent given it
#: may drive the fleet -- over REST today just as much as over MCP. The surface
#: is exactly as narrow as the role is, which is the property being tested.
_AGENT_ROLE = Role(
    name="agent",
    permissions=frozenset({Permission(resource_type="tool", action="invoke")}),
    description="Invokes tools, administers nothing",
)


def _principal(name: str = "user:alice") -> Principal:
    return Principal(id=PrincipalId(name), type=PrincipalType.USER)


def _ctx(principal: Principal | None) -> SimpleNamespace:
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth))))


def _components(principal: Principal, role: str | None, *, enabled: bool = True) -> SimpleNamespace:
    store = InMemoryRoleStore()
    store.add_role(_AGENT_ROLE)
    if role is not None:
        store.assign_role(principal_id=str(principal.id), role_name=role)
    return SimpleNamespace(
        enabled=enabled,
        authz_middleware=AuthorizationMiddleware(authorizer=RBACAuthorizer(store)),
    )


@pytest.fixture(autouse=True)
def reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture()
def as_role(monkeypatch):
    def install(role: str | None, *, enabled: bool = True, name: str = "user:alice"):
        principal = _principal(name)
        monkeypatch.setattr(
            "mcp_hangar.server.context.get_context",
            lambda: SimpleNamespace(auth_components=_components(principal, role, enabled=enabled)),
        )
        return principal

    return install


class TestWhoSeesTheControlPlane:
    def test_an_agent_principal_sees_none_of_it(self, as_role):
        """A role that may invoke tools and administer nothing gets no surface."""
        principal = as_role("agent")

        assert management_tools_for(_ctx(principal)) == frozenset()

    def test_an_operator_sees_what_its_role_permits_and_no_more(self, as_role):
        """provider-admin holds mcp_servers:read but not :lifecycle.

        So it reads the fleet here and cannot start or stop a server -- which is
        exactly what it can and cannot do over REST. The mirroring is the point:
        the same identity gets the same answer whichever door it uses.
        """
        principal = as_role("provider-admin")

        permitted = management_tools_for(_ctx(principal))

        assert "hangar_list" in permitted
        assert "hangar_details" in permitted
        assert "hangar_start" not in permitted
        assert "hangar_stop" not in permitted

    def test_an_admin_sees_the_configuration_tools_an_operator_does_not(self, as_role):
        """provider-admin deliberately stops short of config:reload."""
        assert "hangar_reload_config" in management_tools_for(_ctx(as_role("admin")))
        assert "hangar_reload_config" not in management_tools_for(_ctx(as_role("provider-admin")))

    def test_an_anonymous_caller_sees_none_of_it(self, as_role):
        as_role("admin")

        assert management_tools_for(_ctx(Principal.anonymous())) == frozenset()
        assert management_tools_for(_ctx(None)) == frozenset()

    def test_auth_off_projects_no_control_plane(self, as_role):
        """Stricter than the invoke rule, on purpose.

        `authorize_tool` allows everything when auth is off, because
        `--unsafe-no-auth` depends on it. Projecting on that rule would hand an
        unauthenticated front-door caller the whole meta-API, which is a surface
        it does not have today.
        """
        principal = as_role("admin", enabled=False)

        assert management_tools_for(_ctx(principal)) == frozenset()

    def test_the_invoke_path_tools_are_not_management(self, as_role):
        """The flat names are how a tool is called on a front door."""
        permitted = management_tools_for(_ctx(as_role("admin")))

        assert "hangar_call" not in permitted
        assert "hangar_fetch_continuation" not in permitted
        assert "hangar_delete_continuation" not in permitted


def _principal_with_tenant(tenant: str = "tenant:a") -> Principal:
    return Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER, tenant_id=tenant)


def _front_door(monkeypatch, role: str | None, *, enabled: bool = True):
    """A real front-door server with one upstream tool, and its installed handlers.

    Built through `build_serving_mcp_server`, which is what `mcp-hangar serve`
    builds, and read back through `get_request_handler` -- so these are the
    closures production runs, not re-implementations of them.
    """
    from mcp_hangar._sdk_compat import lowlevel_server
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    get_tool_access_resolver().set_topology_mode("front_door")
    server = build_serving_mcp_server()

    get_tool_projection_registry().build_from_tools(
        _SERVER,
        [ToolSchema(name=_UPSTREAM_TOOL, description="Refund a payment", input_schema={"type": "object"})],
    )

    principal = _principal_with_tenant()
    monkeypatch.setattr(
        "mcp_hangar.server.context.get_context",
        lambda: SimpleNamespace(auth_components=_components(principal, role, enabled=enabled)),
    )

    low = lowlevel_server(server)
    return SimpleNamespace(
        server=server,
        principal=principal,
        list_tools=low.get_request_handler("tools/list").handler,
        call_tool=low.get_request_handler("tools/call").handler,
    )


def _request_ctx(principal: Principal | None) -> SimpleNamespace:
    """The shape the SDK hands a lowlevel handler."""
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth)))


class TestTheProjectionTheFrontDoorActuallyServes:
    """Driving the installed handlers, because a helper that works proves nothing."""

    async def test_an_agent_gets_upstream_tools_and_no_control_plane(self, monkeypatch):
        fd = _front_door(monkeypatch, "agent")

        result = await fd.list_tools(_request_ctx(fd.principal), None)
        names = {tool.name for tool in result.tools}

        assert _UPSTREAM_TOOL in names
        assert not any(name.startswith("hangar_") for name in names)

    async def test_an_operator_gets_both_from_the_same_endpoint(self, monkeypatch):
        """The acceptance criterion: one deployment, two answers."""
        fd = _front_door(monkeypatch, "provider-admin")

        result = await fd.list_tools(_request_ctx(fd.principal), None)
        names = {tool.name for tool in result.tools}

        assert _UPSTREAM_TOOL in names
        assert "hangar_list" in names
        assert "hangar_details" in names

    async def test_an_unauthenticated_caller_gets_nothing_new(self, monkeypatch):
        """Front door with no identity showed an empty list; it still does."""
        fd = _front_door(monkeypatch, "admin")

        result = await fd.list_tools(_request_ctx(None), None)
        names = {tool.name for tool in result.tools}

        assert not any(name.startswith("hangar_") for name in names)

    async def test_a_projected_management_tool_is_callable(self, monkeypatch):
        """Shown implies callable, and the registered tool body is what runs."""
        fd = _front_door(monkeypatch, "provider-admin")
        params = SimpleNamespace(name="hangar_list", arguments={})

        result = await fd.call_tool(_request_ctx(fd.principal), params)

        # The registered body ran: the reply is a tool result, and its text comes
        # from `hangar_list` reaching the query bus -- which this test does not
        # stand up, so it is the tool's own failure and not a routing one. That
        # distinction is the assertion: a name the front door does not dispatch
        # raises -32601 before any body is entered (see the test below).
        assert result.content, "dispatch produced no tool result at all"
        assert "not found" not in str(result).lower()
        assert "ListMcpServersQuery" in str(result)

    async def test_a_management_tool_the_caller_may_not_see_is_not_found(self, monkeypatch):
        """Not shown implies not callable, by name, with the same -32601."""
        fd = _front_door(monkeypatch, "agent")
        params = SimpleNamespace(name="hangar_stop", arguments={"mcp_server": _SERVER})

        with pytest.raises(Exception) as excinfo:
            await fd.call_tool(_request_ctx(fd.principal), params)

        assert "not found" in str(excinfo.value).lower()

    async def test_an_unknown_name_is_still_not_found(self, monkeypatch):
        fd = _front_door(monkeypatch, "admin")
        params = SimpleNamespace(name="not_a_tool", arguments={})

        with pytest.raises(Exception) as excinfo:
            await fd.call_tool(_request_ctx(fd.principal), params)

        assert "not found" in str(excinfo.value).lower()
