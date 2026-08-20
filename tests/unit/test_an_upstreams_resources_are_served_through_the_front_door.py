"""An upstream's resources are served through the front door (#1025, split from #889).

#1021 made ``resources/list`` answer with the caller's handed-out links only.
This covers the full catalogue: per-tenant aggregation across the tenant's own
upstreams for both ``resources/list`` and ``resources/templates/list``, and
``resources/read`` for anything in it.

The decision the catalogue turns on is URI ownership. A URI carries no owning
upstream and two upstreams may serve the same one, so every projected URI is
namespaced as ``hangar://<upstream>/<uri>`` -- unconditionally, so a URI does
not change shape when an unrelated upstream appears and the handed-out-link
path agrees with the catalogue by construction. Nothing is dropped on
collision: both upstreams' resources stay listed under distinct URIs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import resource_link_read_through as rt


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def _clean_links():
    rt._links.clear()
    yield
    rt._links.clear()


@pytest.fixture(autouse=True)
def _front_door():
    with patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True):
        yield


_DOC = {"uri": "demo://doc/1", "name": "Doc 1", "mimeType": "text/plain"}
_TEMPLATE = {"uriTemplate": "demo://doc/{id}", "name": "Doc by id"}


def _catalog(tenant_id, listing, responses):
    with (
        patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=list(responses)),
        patch.object(rt, "_relay_list", side_effect=lambda server, _method: responses[server]),
    ):
        return rt._build_catalog(tenant_id, listing)


class TestCatalog:
    def test_resources_aggregate_across_the_tenants_upstreams(self) -> None:
        entries = _catalog(
            "tenant:a",
            rt.RESOURCES,
            {
                "server_a": {"result": {"resources": [_DOC]}},
                "server_b": {"result": {"resources": [{"uri": "demo://other", "name": "Other"}]}},
            },
        )

        assert [e["uri"] for e in entries] == ["hangar://server_a/demo://doc/1", "hangar://server_b/demo://other"]
        assert entries[0]["name"] == "Doc 1", "the rest of the resource is carried through untouched"

    def test_the_same_uri_from_two_upstreams_keeps_both(self) -> None:
        """The tool-side drop-both rule does NOT carry over: nothing disappears."""
        entries = _catalog(
            "tenant:a",
            rt.RESOURCES,
            {
                "server_a": {"result": {"resources": [_DOC]}},
                "server_b": {"result": {"resources": [_DOC]}},
            },
        )

        assert [e["uri"] for e in entries] == ["hangar://server_a/demo://doc/1", "hangar://server_b/demo://doc/1"]

    def test_templates_aggregate_the_same_way(self) -> None:
        entries = _catalog("tenant:a", rt.TEMPLATES, {"server_a": {"result": {"resourceTemplates": [_TEMPLATE]}}})

        assert [e["uriTemplate"] for e in entries] == ["hangar://server_a/demo://doc/{id}"]

    def test_a_dead_upstream_does_not_empty_the_catalog(self) -> None:
        def relay(server, _method):
            if server == "server_a":
                raise RuntimeError("relay unavailable")
            return {"result": {"resources": [_DOC]}}

        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a", "server_b"]),
            patch.object(rt, "_relay_list", side_effect=relay),
        ):
            entries = rt._build_catalog("tenant:a", rt.RESOURCES)

        assert [e["uri"] for e in entries] == ["hangar://server_b/demo://doc/1"]

    def test_an_upstream_without_resources_contributes_nothing(self) -> None:
        entries = _catalog("tenant:a", rt.RESOURCES, {"server_a": {"error": {"code": -32601}}})
        assert entries == []

    def test_scope_is_the_tenants_own_upstreams(self) -> None:
        """Same per-tenant derivation as the prompts proxy -- no cross-tenant read."""
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[]) as upstreams,
            patch.object(rt, "_relay_list") as relay,
        ):
            assert rt._build_catalog("tenant:b", rt.RESOURCES) == []

        upstreams.assert_called_once_with("tenant:b")
        relay.assert_not_called()


class _FakeLow:
    def __init__(self):
        self.handlers = {}

    def add_request_handler(self, method, _params_type, handler):
        self.handlers[method] = handler


def _register() -> _FakeLow:
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch("mcp_hangar.fastmcp_server.resource_link_read_through.lowlevel_server", return_value=low),
    ):
        assert rt.maybe_register_resource_read_through(MagicMock())
    return low


async def _call(handler, params, tenant_id="tenant:a"):
    token = identity_context_var.set(_identity(tenant_id))
    try:
        return await handler(None, params)
    finally:
        identity_context_var.reset(token)


class TestHandlers:
    @pytest.mark.asyncio
    async def test_list_serves_the_catalog(self) -> None:
        low = _register()
        with patch.object(rt, "_build_catalog", return_value=[{"uri": "hangar://server_a/demo://doc/1", "name": "d"}]):
            result = await _call(low.handlers["resources/list"], SimpleNamespace())

        assert [r.uri for r in result.resources] == ["hangar://server_a/demo://doc/1"]
        assert result.cache_scope == "private", "per-tenant cacheScope, same as tools/list"

    @pytest.mark.asyncio
    async def test_list_is_a_superset_of_the_handed_out_links(self) -> None:
        """A dynamic upstream may never list a link it handed out; it still shows."""
        rt.project_result_uris(
            "tenant:a", "server_a", {"content": [{"type": "resource_link", "uri": "demo://dynamic/1"}]}
        )
        low = _register()
        with patch.object(rt, "_build_catalog", return_value=[{"uri": "hangar://server_a/demo://doc/1", "name": "d"}]):
            result = await _call(low.handlers["resources/list"], SimpleNamespace())

        assert [r.uri for r in result.resources] == [
            "hangar://server_a/demo://doc/1",
            "hangar://server_a/demo://dynamic/1",
        ]

    @pytest.mark.asyncio
    async def test_a_catalog_entry_is_not_listed_twice(self) -> None:
        listed = {"uri": "hangar://server_a/demo://doc/1", "name": "d"}
        rt.project_result_uris("tenant:a", "server_a", {"content": [{"type": "resource_link", "uri": "demo://doc/1"}]})
        low = _register()
        with patch.object(rt, "_build_catalog", return_value=[listed]):
            result = await _call(low.handlers["resources/list"], SimpleNamespace())

        assert [r.uri for r in result.resources] == ["hangar://server_a/demo://doc/1"]

    @pytest.mark.asyncio
    async def test_templates_list_serves_the_catalog(self) -> None:
        low = _register()
        with patch.object(
            rt, "_build_catalog", return_value=[{"uriTemplate": "hangar://server_a/demo://doc/{id}", "name": "t"}]
        ) as build:
            result = await _call(low.handlers["resources/templates/list"], SimpleNamespace())

        assert build.call_args.args[1] == rt.TEMPLATES
        assert [t.uri_template for t in result.resource_templates] == ["hangar://server_a/demo://doc/{id}"]

    @pytest.mark.asyncio
    async def test_read_reaches_a_catalog_resource_never_handed_out(self) -> None:
        """The #1025 widening: read is no longer limited to handed-out links."""
        low = _register()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch.object(
                rt, "_relay_read", return_value={"result": {"contents": [{"uri": "demo://doc/1", "text": "body"}]}}
            ) as relay,
        ):
            result = await _call(low.handlers["resources/read"], SimpleNamespace(uri="hangar://server_a/demo://doc/1"))

        relay.assert_called_once_with("server_a", "demo://doc/1")
        assert result.contents[0].text == "body"

    @pytest.mark.asyncio
    async def test_read_refuses_an_upstream_the_tenant_does_not_project(self) -> None:
        low = _register()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch.object(rt, "_relay_read") as relay,
            pytest.raises(Exception) as excinfo,
        ):
            await _call(low.handlers["resources/read"], SimpleNamespace(uri="hangar://server_z/demo://doc/1"))

        relay.assert_not_called()
        assert "Unknown resource" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_a_handed_out_link_survives_a_later_collision(self) -> None:
        """#1021's promise under a #1025 collision: the link keeps its owner.

        ``server_b`` later publishes the very same upstream uri. Because the
        rewrite is applied at hand-out time, the two never share a projected
        uri, and the handed-out one still routes to ``server_a``.
        """
        rt.project_result_uris("tenant:a", "server_a", {"content": [{"type": "resource_link", "uri": "demo://doc/1"}]})
        low = _register()

        catalog = _catalog(
            "tenant:a",
            rt.RESOURCES,
            {"server_b": {"result": {"resources": [_DOC]}}},
        )
        assert [e["uri"] for e in catalog] == ["hangar://server_b/demo://doc/1"]

        # server_a is no longer among the tenant's upstreams: only the
        # handed-out link keeps this readable, and it must still route to A.
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_b"]),
            patch.object(rt, "_relay_read", return_value={"result": {"contents": []}}) as relay,
        ):
            await _call(low.handlers["resources/read"], SimpleNamespace(uri="hangar://server_a/demo://doc/1"))

        relay.assert_called_once_with("server_a", "demo://doc/1")

    @pytest.mark.asyncio
    async def test_an_upstream_error_surfaces_as_an_mcp_error(self) -> None:
        low = _register()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch.object(rt, "_relay_read", return_value={"error": {"code": -32002, "message": "gone"}}),
            pytest.raises(Exception) as excinfo,
        ):
            await _call(low.handlers["resources/read"], SimpleNamespace(uri="hangar://server_a/demo://doc/1"))

        assert "gone" in str(excinfo.value)
