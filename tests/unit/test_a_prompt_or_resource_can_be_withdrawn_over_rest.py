"""A prompt or a resource can be withdrawn over REST, not only from a file (#1141, #1137).

The overlay has been keyed ``(mcp_server, kind, name)`` since 2.13.0 and the
admin endpoints never passed a kind, so ``POST .../withdraw`` for a prompt
answered ``{"withdrawn": true}``, left the prompt served, and withdrew the
same-named TOOL for that tenant. Everything here is driven through the real
endpoint and then the real ``prompts/*`` / ``resources/*`` / ``tools/list``
surfaces -- the registry has been right all along and proves nothing.

A resource is named by its UPSTREAM uri (``demo://doc/1``), the form
``withdrawn_resources:`` reads, and that uri carries slashes: the route's
name segment is a ``path`` converter and the permission table agrees.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import ToolRestored, ToolWithdrawn
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import prompt_proxy as pp
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.fastmcp_server.flat_tool_projection import _build_flat_map

_SERVER = "server_a"
_TENANT = "tenant:a"
_OTHER = "tenant:b"
_SEARCH = {"name": "search", "description": "Search prompt"}
_DOC = {"uri": "demo://doc/1", "name": "Doc 1"}
_PROJECTED_DOC = f"hangar://{_SERVER}/demo://doc/1"


@pytest.fixture(autouse=True)
def _clean_state():
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    rt._links.clear()
    with patch("mcp_hangar.fastmcp_server.flat_tool_projection._member_to_group", return_value={}):
        yield
    rt._links.clear()
    reset_tool_access_resolver()
    reset_tool_projection_registry()


@pytest.fixture()
def api():
    """The admin API with auth off, plus the event bus it publishes to."""
    from mcp_hangar.server.api.router import create_api_router

    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.auth_components = None
    with (
        patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.admin_tools.get_context", return_value=ctx),
    ):
        yield TestClient(create_api_router(auth_components=None), raise_server_exceptions=False), ctx.event_bus


def _withdraw(client, name, kind=None, tenant_id=_TENANT, verb="withdraw"):
    body = {"tenant_id": tenant_id}
    if kind is not None:
        body["kind"] = kind
    return client.post(f"/admin/tools/{_SERVER}/{name}/{verb}", json=body)


def _identity(tenant_id):
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


class _FakeLow:
    def __init__(self):
        self.handlers = {}

    def add_request_handler(self, method, _params_type, handler):
        self.handlers[method] = handler


def _serve(handler, params, tenant_id=_TENANT):
    import asyncio

    token = identity_context_var.set(_identity(tenant_id))
    try:
        return asyncio.run(handler(None, params))
    finally:
        identity_context_var.reset(token)


# --- the prompts surface ------------------------------------------------------


def _prompt_names(tenant_id=_TENANT):
    with (
        patch.object(pp, "_upstream_ids", return_value=[_SERVER]),
        patch.object(pp, "_relay", return_value={"result": {"prompts": [_SEARCH]}}),
    ):
        return list(pp._build_prompt_map(tenant_id))


def _prompts_get(tenant_id=_TENANT):
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch.object(pp, "lowlevel_server", return_value=low),
    ):
        pp.maybe_register_prompt_proxy(MagicMock())
    with (
        patch.object(pp, "_upstream_ids", return_value=[_SERVER]),
        patch.object(pp, "_relay", return_value={"result": {"prompts": [_SEARCH], "messages": []}}),
    ):
        return _serve(low.handlers["prompts/get"], SimpleNamespace(name="search", arguments=None), tenant_id)


# --- the resources surface ----------------------------------------------------


def _resource_uris(tenant_id=_TENANT):
    with (
        patch.object(pp, "_upstream_ids", return_value=[_SERVER]),
        patch.object(rt, "_relay_list", return_value={"result": {"resources": [_DOC]}}),
    ):
        return [e["uri"] for e in rt._build_catalog(tenant_id, rt.RESOURCES)]


def _resources_read(tenant_id=_TENANT):
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch.object(rt, "lowlevel_server", return_value=low),
    ):
        rt.maybe_register_resource_read_through(MagicMock())
    with (
        patch.object(pp, "_upstream_ids", return_value=[_SERVER]),
        patch.object(rt, "_relay_read", return_value={"result": {"contents": [{"uri": "demo://doc/1", "text": "x"}]}}),
    ):
        return _serve(low.handlers["resources/read"], SimpleNamespace(uri=_PROJECTED_DOC), tenant_id)


class TestAPromptIsWithdrawnOverRest:
    def test_the_prompt_leaves_list_and_get_without_a_reload(self, api):
        client, _ = api
        assert _prompt_names() == ["search"]

        response = _withdraw(client, "search", kind="prompt")

        assert response.status_code == 200
        assert response.json() == {
            "withdrawn": True,
            "mcp_server": _SERVER,
            "tool": "search",
            "kind": "prompt",
            "tenant_id": _TENANT,
        }
        assert _prompt_names() == []
        with pytest.raises(Exception, match="Unknown prompt"):
            _prompts_get()
        assert _prompt_names(_OTHER) == ["search"], "for that tenant only"

    def test_a_tool_of_the_same_name_stays_callable(self, api):
        """The #1137 collateral: the write used to land on the TOOL's overlay."""
        client, _ = api
        get_tool_projection_registry().build_from_tools(
            _SERVER, [ToolSchema(name="search", description="Search tool", input_schema={"type": "object"})]
        )
        assert "search" in _build_flat_map(_TENANT)

        _withdraw(client, "search", kind="prompt")

        assert _prompt_names() == []
        assert "search" in _build_flat_map(_TENANT), "the tool is untouched"
        assert not get_tool_projection_registry().is_withdrawn(_SERVER, "search", kind="tool", tenant_id=_TENANT)

    def test_restore_brings_it_back(self, api):
        client, _ = api
        _withdraw(client, "search", kind="prompt")
        assert _prompt_names() == []

        response = _withdraw(client, "search", kind="prompt", verb="restore")

        assert response.status_code == 200
        assert response.json()["kind"] == "prompt"
        assert _prompt_names() == ["search"]
        assert _prompts_get() is not None

    def test_restore_leaves_a_config_declared_withdrawal_in_force(self, api):
        client, _ = api
        get_tool_projection_registry().set_config_withdrawal(_SERVER, "search", tenant_id=_TENANT, kind="prompt")
        _withdraw(client, "search", kind="prompt")

        _withdraw(client, "search", kind="prompt", verb="restore")

        assert _prompt_names() == [], "effective = config OR runtime"


class TestAResourceIsWithdrawnOverRestByItsUpstreamUri:
    def test_the_resource_leaves_list_and_read(self, api):
        client, _ = api
        assert _resource_uris() == [_PROJECTED_DOC]
        assert _resources_read().contents[0].text == "x"

        response = _withdraw(client, "demo://doc/1", kind="resource")

        assert response.status_code == 200, response.text
        assert response.json()["tool"] == "demo://doc/1", "the slashes rode the path segment intact"
        assert _resource_uris() == []
        with pytest.raises(Exception, match="Unknown resource"):
            _resources_read()
        assert _resource_uris(_OTHER) == [_PROJECTED_DOC]

    def test_a_file_uri_reaches_the_endpoint(self, api):
        """`file:///data/x.txt` -- three slashes -- was a 404 under the `str` converter."""
        client, _ = api

        response = _withdraw(client, "file:///data/x.txt", kind="resource")

        assert response.status_code == 200, response.text
        assert response.json()["tool"] == "file:///data/x.txt"
        assert get_tool_projection_registry().is_withdrawn(
            _SERVER, "file:///data/x.txt", kind="resource", tenant_id=_TENANT
        )

    def test_restore_brings_it_back(self, api):
        client, _ = api
        _withdraw(client, "demo://doc/1", kind="resource")
        assert _resource_uris() == []

        response = _withdraw(client, "demo://doc/1", kind="resource", verb="restore")

        assert response.status_code == 200
        assert _resource_uris() == [_PROJECTED_DOC]
        assert _resources_read().contents[0].text == "x"

    def test_restore_leaves_a_config_declared_withdrawal_in_force(self, api):
        client, _ = api
        get_tool_projection_registry().set_config_withdrawal(
            _SERVER, "demo://doc/1", tenant_id=_TENANT, kind="resource"
        )
        _withdraw(client, "demo://doc/1", kind="resource")

        _withdraw(client, "demo://doc/1", kind="resource", verb="restore")

        assert _resource_uris() == []


class TestTheKindIsValidated:
    @pytest.mark.parametrize("verb", ["withdraw", "restore"])
    @pytest.mark.parametrize("kind", ["prompts", "Tool", "", None, 7])
    def test_an_unknown_kind_is_a_400_and_writes_nothing(self, api, verb, kind):
        client, event_bus = api

        response = client.post(f"/admin/tools/{_SERVER}/search/{verb}", json={"tenant_id": _TENANT, "kind": kind})

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_kind"
        registry = get_tool_projection_registry()
        for k in ("tool", "prompt", "resource"):
            assert not registry.is_withdrawn(_SERVER, "search", kind=k, tenant_id=_TENANT)
        event_bus.publish.assert_not_called()

    def test_no_kind_still_withdraws_a_tool(self, api):
        """A caller written before this field is unchanged."""
        client, event_bus = api

        response = _withdraw(client, "search")

        assert response.json()["kind"] == "tool"
        registry = get_tool_projection_registry()
        assert registry.is_withdrawn(_SERVER, "search", kind="tool", tenant_id=_TENANT)
        assert not registry.is_withdrawn(_SERVER, "search", kind="prompt", tenant_id=_TENANT)
        assert event_bus.publish.call_args.args[0].kind == "tool"

    @pytest.mark.parametrize(("verb", "event_class"), [("withdraw", ToolWithdrawn), ("restore", ToolRestored)])
    def test_the_event_carries_the_requested_kind(self, api, verb, event_class):
        client, event_bus = api

        _withdraw(client, "demo://doc/1", kind="resource", verb=verb)

        event = event_bus.publish.call_args.args[0]
        assert isinstance(event, event_class)
        assert (event.mcp_server, event.tool, event.kind, event.tenant_id) == (
            _SERVER,
            "demo://doc/1",
            "resource",
            _TENANT,
        )
