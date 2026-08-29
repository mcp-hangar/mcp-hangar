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

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.server.config import _init_resource_links_from_config
from mcp_hangar.server.config_schema import validate_config


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
        monkeypatch.setattr(rt, "_MAX_LINKS_PER_TENANT", 2)
        for i in range(3):
            rt.project_result_uris("t", "s", {"content": [{"type": "resource_link", "uri": f"demo://r/{i}"}]})

        assert rt._lookup("t", "hangar://s/demo://r/0") is None, "oldest reference evicted first"
        assert rt._lookup("t", "hangar://s/demo://r/2") is not None

    def test_the_tenant_map_count_is_bounded(self, monkeypatch) -> None:
        """#1139: minting tenants must not trade one exhaustion for another."""
        monkeypatch.setattr(rt, "_MAX_TENANTS", 2)
        for tenant in ("t0", "t1", "t2"):
            rt.project_result_uris(tenant, "s", {"content": [{"type": "resource_link", "uri": "demo://r"}]})

        assert list(rt._links) == ["t1", "t2"], "least recently used tenant map evicted first"
        assert rt._lookup("t0", "hangar://s/demo://r") is None
        assert rt._lookup("t2", "hangar://s/demo://r") is not None

    def test_payloads_without_links_are_ignored(self) -> None:
        rt.project_result_uris("t", "s", None)
        rt.project_result_uris("t", "s", "text")
        rt.project_result_uris("t", "s", {"content": "not-a-list"})
        assert rt._links == {}


def _evicted(reason: str) -> float:
    """Read the counter the way a scrape would; other tests may have advanced it."""
    return next(
        (
            sample.value
            for sample in prometheus_metrics.RESOURCE_LINKS_EVICTED_TOTAL.collect()
            if sample.labels.get("reason") == reason
        ),
        0.0,
    )


def _hand_out(tenant: str, n: int) -> None:
    for i in range(n):
        rt.project_result_uris(tenant, "s", {"content": [{"type": "resource_link", "uri": f"demo://r/{i}"}]})


class TestEvictionsAreCounted:
    """#1145: an eviction with no record is the shape #1128 argued against."""

    def test_the_counter_is_scraped(self) -> None:
        prometheus_metrics.record_resource_links_evicted("tenant_cap", 1)
        assert "mcp_hangar_resource_links_evicted_total" in prometheus_metrics.get_metrics()

    def test_only_a_reason_label_never_a_tenant(self) -> None:
        assert prometheus_metrics.RESOURCE_LINKS_EVICTED_TOTAL.label_names == ["reason"]

    def test_a_write_below_the_cap_counts_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(rt, "_MAX_LINKS_PER_TENANT", 2)
        before = _evicted("tenant_cap")
        _hand_out("t", 2)
        assert _evicted("tenant_cap") == before

    def test_the_per_tenant_cap_counts_each_evicted_link(self, monkeypatch) -> None:
        monkeypatch.setattr(rt, "_MAX_LINKS_PER_TENANT", 2)
        before = _evicted("tenant_cap")
        _hand_out("t", 5)
        assert _evicted("tenant_cap") == before + 3

    def test_the_tenant_map_cap_counts_the_dropped_tenants_links(self, monkeypatch) -> None:
        monkeypatch.setattr(rt, "_MAX_TENANTS", 1)
        _hand_out("t0", 3)
        before_map, before_cap = _evicted("tenant_map_cap"), _evicted("tenant_cap")
        _hand_out("t1", 1)
        assert _evicted("tenant_map_cap") == before_map + 3, "what went away is every link t0 held"
        assert _evicted("tenant_cap") == before_cap


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

    @pytest.mark.asyncio
    async def test_another_tenants_traffic_does_not_evict_a_link(self) -> None:
        """#1139: a tenant's links are bounded by its own behaviour, not B's.

        Driven through the real read and list handlers with the catalogue and
        the projected-upstream route both empty, so the remembered map is the
        only thing that can carry the answer -- the case the map exists for.
        """
        rt.project_result_uris("tenant:a", "server_a", {"content": [_link()]})
        for i in range(rt._MAX_LINKS_PER_TENANT * 2):
            rt.project_result_uris(
                "tenant:b", "server_a", {"content": [{"type": "resource_link", "uri": f"demo://{i}"}]}
            )
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[]),
                patch.object(rt, "_build_catalog", return_value=[]),
                patch.object(
                    rt, "_relay_read", return_value={"result": {"contents": [{"uri": "demo://blob/1", "text": "p"}]}}
                ),
            ):
                listed = await low.handlers["resources/list"](None, SimpleNamespace())
                read = await low.handlers["resources/read"](None, SimpleNamespace(uri=_PROJECTED))
        finally:
            identity_context_var.reset(token)

        assert [str(r.uri) for r in listed.resources] == [_PROJECTED], "still listed"
        assert str(read.contents[0].uri) == _PROJECTED, "still resolvable"

    def test_egress_mode_registers_nothing(self) -> None:
        low = _register(resolver_mode="egress")
        assert low.handlers == {}


class TestTheConfigSurface:
    """#1146: `resource_links.max_per_tenant` is the operator's lever on the cap."""

    @pytest.fixture(autouse=True)
    def _restore_cap(self, monkeypatch) -> None:
        # Same-value setattr: teardown restores whatever the module held before.
        monkeypatch.setattr(rt, "_MAX_LINKS_PER_TENANT", rt._MAX_LINKS_PER_TENANT)

    @pytest.mark.asyncio
    async def test_setting_the_key_changes_the_effective_cap(self) -> None:
        """Verified at the surface: with the cap at 2, a tenant's third link
        pushes its first out of both `resources/list` and `resources/read`."""
        _init_resource_links_from_config({"resource_links": {"max_per_tenant": 2}})
        first = _link()
        rt.project_result_uris("tenant:a", "server_a", {"content": [first]})
        for i in range(2):
            rt.project_result_uris(
                "tenant:a", "server_a", {"content": [{"type": "resource_link", "uri": f"demo://{i}"}]}
            )
        low = _register()
        token = identity_context_var.set(_identity("tenant:a"))
        try:
            with (
                patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[]),
                patch.object(rt, "_build_catalog", return_value=[]),
            ):
                listed = await low.handlers["resources/list"](None, SimpleNamespace())
                with pytest.raises(Exception, match="Unknown resource"):
                    await low.handlers["resources/read"](None, SimpleNamespace(uri=_PROJECTED))
        finally:
            identity_context_var.reset(token)

        assert [str(r.uri) for r in listed.resources] == ["hangar://server_a/demo://0", "hangar://server_a/demo://1"]

    def test_omitting_the_key_keeps_the_default(self) -> None:
        _init_resource_links_from_config({"resource_links": {"max_per_tenant": 2}})
        _init_resource_links_from_config({})

        assert rt._MAX_LINKS_PER_TENANT == 4096
        assert rt.DEFAULT_MAX_LINKS_PER_TENANT == 4096

    @pytest.mark.parametrize("raw", ["4096", 0, -1, True, 2.5])
    def test_an_invalid_value_refuses_to_start(self, raw) -> None:
        """A limit that quietly falls back reads as applied. `True` is the trap:
        it IS an int to `isinstance`, and `yes` in YAML is `True`."""
        with pytest.raises(ConfigurationError, match="resource_links.max_per_tenant"):
            _init_resource_links_from_config({"resource_links": {"max_per_tenant": raw}})

    def test_the_section_is_known_to_the_schema(self) -> None:
        """A key nothing reads is the failure `validate_config` exists to catch."""
        assert validate_config({"resource_links": {"max_per_tenant": 2}}) == []
        assert validate_config({"resource_links": {"max_per_tenantt": 2}}) != []
        assert validate_config({"resource_linkss": {"max_per_tenant": 2}}) != []
