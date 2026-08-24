"""A `ui://` resource can be allowlisted and consented to, not only denied (#1048).

SEP-1865 mandates a human decision before a `ui://` resource reaches a client
webview, and the guard has stated that mandate since it was written. Both halves
that satisfy it were missing: nothing built a `UiResourcePolicy`, so no tenant
had an allowlist, and no `UiConsentGate` was ever attached, so an allowlisted
resource was refused for want of anyone to ask. A control that can only deny is
not a control an operator can configure -- and ADR-024 records this consent as
the one human decision that belongs on a fetch, which it only is if it can be
made.

Both halves still fail closed on their own, and each direction is pinned here:
no allowlist entry denies before consent is asked for, and an allowlisted
resource with no gate attached is denied too.

Driven through the registered `resources/read` and `resources/list` handlers and
through `load_configuration`, not through `UiResourceGuard.enforce()` directly:
the guard was never the broken part.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.ui_resource_guard import (
    UiResourceGuard,
    get_ui_resource_guard,
    reset_ui_resource_guard,
)
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.ui_resource import DEFAULT_UI_CSP, UiResourcePolicy
from mcp_hangar.fastmcp_server import resource_link_read_through as rt

_SERVER = "docs_server"
_TENANT = "tenant:a"
_UI = "ui://reports/q3"
_UI_ENTRY = {"uri": _UI, "name": "Q3 report"}
_DOC_ENTRY = {"uri": "demo://doc/1", "name": "Doc 1"}


@pytest.fixture(autouse=True)
def _clean_guard():
    reset_ui_resource_guard()
    rt._links.clear()
    yield
    rt._links.clear()
    reset_ui_resource_guard()


def _allowlist(*entries: str, csp: str | None = None) -> None:
    policy = (
        UiResourcePolicy(allowlist=frozenset(entries), csp=csp)
        if csp
        else UiResourcePolicy(allowlist=frozenset(entries))
    )
    get_ui_resource_guard()  # create it, then replace its policy map
    from mcp_hangar.domain.services.ui_resource_guard import set_ui_resource_guard

    set_ui_resource_guard(UiResourceGuard({_TENANT: policy}))


class _Consent:
    """A consent gate under the test's control, recording what it was asked."""

    def __init__(self, answer: bool | Exception) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str | None, str]] = []

    async def request_consent(self, uri: str, tenant_id: str | None, mcp_server_id: str, correlation_id: str) -> bool:
        self.asked.append((uri, tenant_id, mcp_server_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _FakeLow:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def add_request_handler(self, method, _params_type, handler) -> None:
        self.handlers[method] = handler


def _register() -> _FakeLow:
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch("mcp_hangar.fastmcp_server.resource_link_read_through.lowlevel_server", return_value=low),
    ):
        assert rt.maybe_register_resource_read_through(MagicMock())
    return low


def _await(coro) -> Any:
    token = identity_context_var.set(
        IdentityContext(
            caller=CallerIdentity(
                user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=_TENANT
            )
        )
    )
    try:
        return asyncio.run(coro)
    finally:
        identity_context_var.reset(token)


def _read(uri: str) -> Any:
    low = _register()
    with (
        patch.object(rt, "_upstream_ids", create=True, return_value=[_SERVER]),
        patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[_SERVER]),
        patch.object(rt, "_relay_read", return_value={"result": {"contents": [{"uri": uri, "text": "<h1/>"}]}}),
    ):
        return _await(low.handlers["resources/read"](MagicMock(), MagicMock(uri=f"hangar://{_SERVER}/{uri}")))


def _list() -> list[dict]:
    low = _register()
    with (
        patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=[_SERVER]),
        patch.object(rt, "_relay_list", return_value={"result": {"resources": [_DOC_ENTRY, _UI_ENTRY]}}),
    ):
        result = _await(low.handlers["resources/list"](MagicMock(), MagicMock()))
    return [entry.model_dump() if hasattr(entry, "model_dump") else entry for entry in result.resources]


class TestBothHalvesAreRequired:
    def test_an_unconfigured_deployment_denies_every_ui_resource(self) -> None:
        """The default guard: no allowlist, so consent is never even asked."""
        consent = _Consent(True)
        get_ui_resource_guard().attach_consent_gate(consent)

        with pytest.raises(Exception, match="Unknown resource|not deliverable"):
            _read(_UI)
        assert consent.asked == [], "denied before anyone was asked"

    def test_an_allowlisted_resource_with_no_gate_attached_is_denied(self) -> None:
        """A mandate with nobody to ask is a denial, not a pass."""
        _allowlist(_UI)

        with pytest.raises(Exception, match="not deliverable"):
            _read(_UI)

    def test_an_allowlisted_and_consented_resource_is_delivered(self) -> None:
        _allowlist("ui://reports/")
        consent = _Consent(True)
        get_ui_resource_guard().attach_consent_gate(consent)

        result = _read(_UI)

        assert consent.asked == [(_UI, _TENANT, _SERVER)], "the guard asks about the UPSTREAM uri"
        assert [c.uri for c in result.contents] == [f"hangar://{_SERVER}/{_UI}"]

    @pytest.mark.parametrize("answer", [False, RuntimeError("gate down")], ids=["refused", "gate error"])
    def test_a_refused_or_broken_consent_denies(self, answer) -> None:
        _allowlist(_UI)
        get_ui_resource_guard().attach_consent_gate(_Consent(answer))

        with pytest.raises(Exception, match="not deliverable"):
            _read(_UI)


class TestTheCatalogueFollowsTheAllowlist:
    def test_a_ui_resource_is_absent_until_allowlisted(self) -> None:
        assert [e["uri"] for e in _list()] == [f"hangar://{_SERVER}/demo://doc/1"]

    def test_an_allowlisted_ui_resource_is_listed_without_asking_for_consent(self) -> None:
        """Listing is the pure decision; consent is a delivery-time question."""
        _allowlist(_UI)
        consent = _Consent(True)
        get_ui_resource_guard().attach_consent_gate(consent)

        assert [e["uri"] for e in _list()] == [
            f"hangar://{_SERVER}/demo://doc/1",
            f"hangar://{_SERVER}/{_UI}",
        ]
        assert consent.asked == [], "a listing must not raise N consent prompts"


class TestTheConfigSurface:
    def _load(self, tmp_path: Path, ui_resources: Any) -> None:
        from mcp_hangar.server.config import load_configuration

        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"mcp_servers": {}, "ui_resources": ui_resources}))
        load_configuration(str(path), load_servers=False)

    def test_a_tenant_allowlist_reaches_the_guard(self, tmp_path: Path) -> None:
        self._load(tmp_path, {"tenants": {_TENANT: {"allowlist": ["ui://reports/"]}}})

        guard = get_ui_resource_guard()
        assert guard.evaluate(_UI, _TENANT).allowed
        assert not guard.evaluate(_UI, "tenant:b").allowed, "another tenant keeps the fail-closed default"

    def test_a_csp_override_is_carried_and_the_default_holds_otherwise(self, tmp_path: Path) -> None:
        self._load(
            tmp_path,
            {
                "tenants": {
                    _TENANT: {"allowlist": [_UI], "csp": "default-src 'none'"},
                    "tenant:b": {"allowlist": [_UI]},
                }
            },
        )

        guard = get_ui_resource_guard()
        assert guard.evaluate(_UI, _TENANT).csp == "default-src 'none'"
        assert guard.evaluate(_UI, "tenant:b").csp == DEFAULT_UI_CSP

    def test_consent_cannot_be_turned_off_from_the_file(self, tmp_path: Path) -> None:
        """SEP-1865 mandates it; the key is not read (ADR-024)."""
        self._load(tmp_path, {"tenants": {_TENANT: {"allowlist": [_UI], "require_consent": False}}})

        assert get_ui_resource_guard().evaluate(_UI, _TENANT).requires_consent

    @pytest.mark.parametrize(
        "spec",
        [{"allowlist": "ui://reports/"}, {"allowlist": [123]}, "not-a-mapping"],
        ids=["allowlist not a list", "entry not a str", "tenant not a mapping"],
    )
    def test_an_unparseable_entry_leaves_that_tenant_denied(self, tmp_path: Path, spec: Any) -> None:
        """The failure of this block cannot open the surface it guards."""
        self._load(tmp_path, {"tenants": {_TENANT: spec}})

        assert not get_ui_resource_guard().evaluate(_UI, _TENANT).allowed

    def test_no_block_at_all_leaves_the_default_guard(self, tmp_path: Path) -> None:
        from mcp_hangar.server.config import load_configuration

        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"mcp_servers": {}}))
        load_configuration(str(path), load_servers=False)

        assert not get_ui_resource_guard().evaluate(_UI, _TENANT).allowed


class TestTheApprovalAdapter:
    def test_it_asks_the_approval_gate_about_the_uri(self) -> None:
        from mcp_hangar.approvals.consent import ApprovalConsentGate

        service = MagicMock()

        async def _check(**kwargs):
            service.seen = kwargs
            return MagicMock(approved=True)

        service.check = _check

        assert asyncio.run(ApprovalConsentGate(service).request_consent(_UI, _TENANT, _SERVER, "corr-1"))
        assert service.seen["tool_name"] == _UI
        assert service.seen["arguments"] == {}
        assert service.seen["tenant_id"] == _TENANT
        assert service.seen["policy"].requires_approval(_UI), "the synthesised policy is what makes it a hold"

    def test_a_denial_is_false_not_an_exception(self) -> None:
        from mcp_hangar.approvals.consent import ApprovalConsentGate

        service = MagicMock()

        async def _check(**_kwargs):
            return MagicMock(approved=False, reason="denied by operator")

        service.check = _check

        assert asyncio.run(ApprovalConsentGate(service).request_consent(_UI, _TENANT, _SERVER, "corr-1")) is False
