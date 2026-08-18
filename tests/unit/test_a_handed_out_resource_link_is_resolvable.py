"""A resource_link the gateway handed out must resolve on the same gateway (#889).

The recorded dead end: a tool result carries
``{"type": "resource_link", "uri": "demo://resource/dynamic/blob/1", ...}``,
faithfully proxied -- and ``resources/read`` for that uri on the very
connection that produced it answered ``Unknown resource``. Handing out a
reference and then refusing it actively misleads the client; this covers the
read-through that closes exactly that, and nothing wider.
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


_LINK = {"type": "resource_link", "uri": "demo://blob/1", "name": "Blob 1", "mimeType": "text/plain"}


class TestRecording:
    def test_links_are_remembered_per_tenant(self) -> None:
        rt.record_resource_links("tenant:a", "server_a", {"content": [_LINK, {"type": "text", "text": "hi"}]})

        assert rt._lookup("tenant:a", "demo://blob/1") == ("server_a", _LINK)
        assert rt._lookup("tenant:b", "demo://blob/1") is None, "a link handed to A must not be readable by B"

    def test_the_map_is_bounded(self, monkeypatch) -> None:
        monkeypatch.setattr(rt, "_MAX_LINKS", 2)
        for i in range(3):
            rt.record_resource_links("t", "s", {"content": [{"type": "resource_link", "uri": f"demo://r/{i}"}]})

        assert rt._lookup("t", "demo://r/0") is None, "oldest reference evicted first"
        assert rt._lookup("t", "demo://r/2") is not None

    def test_non_list_content_is_ignored(self) -> None:
        rt.record_resource_links("t", "s", None)
        rt.record_resource_links("t", "s", "text")
        rt.record_resource_links("t", "s", {"content": "not-a-list"})
        assert rt._links == {}


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
        rt.record_resource_links("tenant:a", "server_a", {"content": [_LINK]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch.object(
                rt,
                "_relay_read",
                return_value={"result": {"contents": [{"uri": "demo://blob/1", "text": "payload"}]}},
            ) as relay:
                result = await low.handlers["resources/read"](None, SimpleNamespace(uri="demo://blob/1"))
        finally:
            identity_context_var.reset(token)

        relay.assert_called_once_with("server_a", "demo://blob/1")
        assert result.contents[0].text == "payload"

    @pytest.mark.asyncio
    async def test_an_unknown_uri_is_still_unknown(self) -> None:
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with pytest.raises(Exception) as excinfo:
                await low.handlers["resources/read"](None, SimpleNamespace(uri="demo://never-handed-out"))
        finally:
            identity_context_var.reset(token)
        assert "Unknown resource" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_another_tenants_link_reads_as_unknown(self) -> None:
        """Capability semantics: existence is not leaked across tenants."""
        rt.record_resource_links("tenant:a", "server_a", {"content": [_LINK]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:b"))
        try:
            with pytest.raises(Exception) as excinfo:
                await low.handlers["resources/read"](None, SimpleNamespace(uri="demo://blob/1"))
        finally:
            identity_context_var.reset(token)
        assert "Unknown resource" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_a_ui_resource_is_denied_fail_closed(self) -> None:
        """SEP-1865: no wired policy means every ui:// resource is refused."""
        ui_link = {"type": "resource_link", "uri": "ui://widget/1", "name": "w"}
        rt.record_resource_links("tenant:a", "server_a", {"content": [ui_link]})
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with patch.object(rt, "_relay_read") as relay:
                with pytest.raises(Exception) as excinfo:
                    await low.handlers["resources/read"](None, SimpleNamespace(uri="ui://widget/1"))
        finally:
            identity_context_var.reset(token)
        relay.assert_not_called()
        assert "not deliverable" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_list_answers_with_the_callers_links_only(self) -> None:
        rt.record_resource_links("tenant:a", "server_a", {"content": [_LINK]})
        rt.record_resource_links(
            "tenant:b", "server_a", {"content": [{"type": "resource_link", "uri": "demo://other"}]}
        )
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            result = await low.handlers["resources/list"](None, SimpleNamespace())
        finally:
            identity_context_var.reset(token)

        assert [str(r.uri) for r in result.resources] == ["demo://blob/1"]

    def test_egress_mode_registers_nothing(self) -> None:
        low = _register(resolver_mode="egress")
        assert low.handlers == {}
