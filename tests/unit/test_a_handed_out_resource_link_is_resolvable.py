"""A resource_link the gateway handed out must resolve on the same gateway (#889).

The recorded dead end: a tool result carries
``{"type": "resource_link", "uri": "demo://resource/dynamic/blob/1", ...}``,
faithfully proxied -- and ``resources/read`` for that uri on the very
connection that produced it answered ``Unknown resource``. Handing out a
reference and then refusing it actively misleads the client; this covers the
read-through that closes exactly that, and nothing wider.

Since #1025 the link is handed out in PROJECTED form (namespaced with its
owning upstream), which is what keeps this path and the catalogue path from
disagreeing -- the catalogue's collision handling is covered in
``test_an_upstreams_resources_are_served_through_the_front_door``.
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
    """The projection is front-door-only; every case here is a front door."""
    with patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True):
        yield


def _link() -> dict[str, str]:
    return {"type": "resource_link", "uri": "demo://blob/1", "name": "Blob 1", "mimeType": "text/plain"}


#: What the client sees for ``demo://blob/1`` handed out by ``server_a``.
_PROJECTED = "hangar://server_a/demo://blob/1"


class TestRecording:
    def test_links_are_remembered_per_tenant(self) -> None:
        result = {"content": [_link(), {"type": "text", "text": "hi"}]}
        rt.project_result_uris("tenant:a", "server_a", result)

        assert result["content"][0]["uri"] == _PROJECTED, "the client is handed the projected uri"
        assert rt._lookup("tenant:a", _PROJECTED) == ("server_a", result["content"][0])
        assert rt._lookup("tenant:b", _PROJECTED) is None, "a link handed to A must not be readable by B"

    def test_an_embedded_resource_is_projected_too(self) -> None:
        result = {"content": [{"type": "resource", "resource": {"uri": "demo://blob/1", "text": "x"}}]}
        rt.project_result_uris("tenant:a", "server_a", result)

        assert result["content"][0]["resource"]["uri"] == _PROJECTED

    def test_nothing_is_rewritten_outside_front_door(self) -> None:
        result = {"content": [_link()]}
        with patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=False):
            rt.project_result_uris("tenant:a", "server_a", result)

        assert result["content"][0]["uri"] == "demo://blob/1"
        assert rt._links == {}

    def test_the_map_is_bounded(self, monkeypatch) -> None:
        monkeypatch.setattr(rt, "_MAX_LINKS", 2)
        for i in range(3):
            rt.project_result_uris("t", "s", {"content": [{"type": "resource_link", "uri": f"demo://r/{i}"}]})

        assert rt._lookup("t", "hangar://s/demo://r/0") is None, "oldest reference evicted first"
        assert rt._lookup("t", "hangar://s/demo://r/2") is not None

    def test_payloads_without_links_are_ignored(self) -> None:
        rt.project_result_uris("t", "s", None)
        rt.project_result_uris("t", "s", "text")
        rt.project_result_uris("t", "s", {"content": "not-a-list"})
        assert rt._links == {}


class TestUriProjection:
    def test_projection_round_trips(self) -> None:
        projected = rt.project_uri("server_a", "demo://blob/1")
        assert projected == _PROJECTED
        assert rt.resolve_uri(projected) == ("server_a", "demo://blob/1")

    def test_a_template_variable_survives_verbatim(self) -> None:
        """RFC 6570 expansion must still work through the rewrite -- no escaping."""
        projected = rt.project_uri("server_a", "demo://blob/{id}")
        assert projected == "hangar://server_a/demo://blob/{id}"
        assert rt.resolve_uri(projected.replace("{id}", "7")) == ("server_a", "demo://blob/7")

    def test_a_non_projected_uri_does_not_resolve(self) -> None:
        assert rt.resolve_uri("demo://blob/1") is None
        assert rt.resolve_uri("hangar://server_a") is None
        assert rt.resolve_uri("hangar:///demo://blob/1") is None


class _FakeLow:
    """Captures add_request_handler registrations (the SDK v2 seam)."""

    def __init__(self):
        self.handlers = {}

    def add_request_handler(self, method, _params_type, handler):
        self.handlers[method] = handler


def _register(resolver_mode: str = "front_door") -> _FakeLow:
    low = _FakeLow()
    mcp = MagicMock()
    with (
        patch(
            "mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=resolver_mode == "front_door"
        ),
        patch("mcp_hangar.fastmcp_server.resource_link_read_through.lowlevel_server", return_value=low),
    ):
        installed = rt.maybe_register_resource_read_through(mcp)
    assert installed == (resolver_mode == "front_door")
    return low


class TestReadThrough:
    @pytest.mark.asyncio
    async def test_a_handed_out_link_resolves(self) -> None:
        rt.project_result_uris("tenant:a", "server_a", {"content": [_link()]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch.object(
                rt,
                "_relay_read",
                return_value={"result": {"contents": [{"uri": "demo://blob/1", "text": "payload"}]}},
            ) as relay:
                result = await low.handlers["resources/read"](None, SimpleNamespace(uri=_PROJECTED))
        finally:
            identity_context_var.reset(token)

        # The upstream is asked with ITS uri, not the projected one.
        relay.assert_called_once_with("server_a", "demo://blob/1")
        assert result.contents[0].text == "payload"
        assert result.contents[0].uri == _PROJECTED, "and answered with the projected one"

    @pytest.mark.asyncio
    async def test_an_unknown_uri_is_still_unknown(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[]):
                with pytest.raises(Exception) as excinfo:
                    await low.handlers["resources/read"](None, SimpleNamespace(uri="demo://never-handed-out"))
        finally:
            identity_context_var.reset(token)
        assert "Unknown resource" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_another_tenants_link_reads_as_unknown(self) -> None:
        """Capability semantics: existence is not leaked across tenants."""
        rt.project_result_uris("tenant:a", "server_a", {"content": [_link()]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:b"))
        try:
            with patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[]):
                with pytest.raises(Exception) as excinfo:
                    await low.handlers["resources/read"](None, SimpleNamespace(uri=_PROJECTED))
        finally:
            identity_context_var.reset(token)
        assert "Unknown resource" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_a_ui_resource_is_denied_fail_closed(self) -> None:
        """SEP-1865: no wired policy means every ui:// resource is refused.

        Also pins that the guard reads the DECODED upstream uri -- namespacing
        hides the ``ui://`` scheme from a guard that looks at what the client
        sent.

        Since #1028 the guard is the first gate inside ``_deliverable``, so the
        refusal arrives as the same generic not-found a nonexistent resource
        gets. That is the stronger answer, not a weaker one: a denial that
        announces itself is an enumeration oracle (#905).
        """
        ui_link = {"type": "resource_link", "uri": "ui://widget/1", "name": "w"}
        rt.project_result_uris("tenant:a", "server_a", {"content": [ui_link]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch.object(rt, "_relay_read") as relay:
                with pytest.raises(Exception) as excinfo:
                    await low.handlers["resources/read"](None, SimpleNamespace(uri="hangar://server_a/ui://widget/1"))
        finally:
            identity_context_var.reset(token)
        relay.assert_not_called()
        assert "Unknown resource" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_list_answers_with_the_callers_links_only(self) -> None:
        rt.project_result_uris("tenant:a", "server_a", {"content": [_link()]})
        rt.project_result_uris("tenant:b", "server_a", {"content": [{"type": "resource_link", "uri": "demo://other"}]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch.object(rt, "_build_catalog", return_value=[]):
                result = await low.handlers["resources/list"](None, SimpleNamespace())
        finally:
            identity_context_var.reset(token)

        assert [str(r.uri) for r in result.resources] == [_PROJECTED]

    def test_egress_mode_registers_nothing(self) -> None:
        low = _register(resolver_mode="egress")
        assert low.handlers == {}
