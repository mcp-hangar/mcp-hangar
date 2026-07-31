"""Tests for the fail-closed ``ui://`` (MCP Apps / SEP-1865) resource guard.

Covers the three enforcement controls, all fail-closed:

- **Allowlist:** a ``ui://`` resource not on the tenant allowlist is denied;
  an empty allowlist denies every ``ui://`` resource (the default posture).
- **CSP + mandatory consent:** an allowlisted ``ui://`` resource carries the
  restrictive CSP and still requires consent -- denied without a consent gate,
  denied when consent is refused / errors, allowed only when consent is granted.
- **Pass-through:** non-``ui://`` resources are unaffected and never touch the
  consent gate.
- **Per-tenant isolation:** tenant A's allowlist does not permit tenant B's
  ``ui://`` resource; an unknown tenant gets the empty default (deny).

All values use NEUTRAL placeholders (no real brand names). No network or backend
I/O -- the guard and its consent gate are constructed directly for isolation.
"""

from __future__ import annotations

from mcp_hangar.domain.services.ui_resource_guard import UiResourceGuard
from mcp_hangar.domain.value_objects.ui_resource import (
    DEFAULT_UI_CSP,
    UiResourcePolicy,
    is_ui_scheme,
    matches_ui_allowlist,
)

# ---------------------------------------------------------------------------
# Neutral placeholders
# ---------------------------------------------------------------------------

_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_SERVER = "srv-alpha"

_UI_A = "ui://reports/summary"
_UI_B = "ui://widgets/chart"
_NON_UI = "https://example.invalid/data.json"
_FILE_URI = "file:///etc/passwd"


# ---------------------------------------------------------------------------
# Consent gate stubs
# ---------------------------------------------------------------------------


class _StubConsentGate:
    """Consent gate returning a fixed decision; records the calls it received."""

    def __init__(self, decision: bool) -> None:
        self._decision = decision
        self.calls: list[tuple[str, str | None, str, str]] = []

    async def request_consent(
        self,
        uri: str,
        tenant_id: str | None,
        mcp_server_id: str,
        correlation_id: str,
    ) -> bool:
        self.calls.append((uri, tenant_id, mcp_server_id, correlation_id))
        return self._decision


class _RaisingConsentGate:
    """Consent gate that raises -- proves the guard fails closed on error."""

    async def request_consent(
        self,
        uri: str,
        tenant_id: str | None,
        mcp_server_id: str,
        correlation_id: str,
    ) -> bool:
        raise RuntimeError("consent backend unavailable")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_is_ui_scheme(self) -> None:
        assert is_ui_scheme(_UI_A) is True
        assert is_ui_scheme("UI://Reports") is True  # scheme is case-insensitive
        assert is_ui_scheme(_NON_UI) is False
        assert is_ui_scheme(_FILE_URI) is False
        assert is_ui_scheme("") is False

    def test_empty_allowlist_matches_nothing(self) -> None:
        assert matches_ui_allowlist(_UI_A, frozenset()) is False

    def test_exact_match(self) -> None:
        assert matches_ui_allowlist(_UI_A, frozenset({_UI_A})) is True
        assert matches_ui_allowlist(_UI_B, frozenset({_UI_A})) is False

    def test_wildcard_prefix_match(self) -> None:
        allow = frozenset({"ui://reports/*"})
        assert matches_ui_allowlist("ui://reports/summary", allow) is True
        assert matches_ui_allowlist("ui://reports/", allow) is True
        assert matches_ui_allowlist("ui://widgets/chart", allow) is False

    def test_origin_prefix_match(self) -> None:
        allow = frozenset({"ui://reports/"})
        assert matches_ui_allowlist("ui://reports/summary", allow) is True
        assert matches_ui_allowlist("ui://reportsX/summary", allow) is False

    def test_non_ui_entry_never_grants(self) -> None:
        # A non-ui:// allowlist entry must never permit a ui:// resource.
        assert matches_ui_allowlist(_UI_A, frozenset({"https://example.invalid/"})) is False


# ---------------------------------------------------------------------------
# Policy value object
# ---------------------------------------------------------------------------


class TestUiResourcePolicy:
    def test_default_is_fail_closed(self) -> None:
        policy = UiResourcePolicy()
        assert policy.allowlist == frozenset()
        assert policy.csp == DEFAULT_UI_CSP
        assert policy.require_consent is True
        assert policy.is_allowed(_UI_A) is False

    def test_allowlisted_uri_is_allowed(self) -> None:
        policy = UiResourcePolicy(allowlist=frozenset({_UI_A}))
        assert policy.is_allowed(_UI_A) is True
        assert policy.is_allowed(_UI_B) is False

    def test_default_csp_is_restrictive(self) -> None:
        # A few load-bearing directives that make this fail-closed.
        assert "default-src 'none'" in DEFAULT_UI_CSP
        assert "connect-src 'none'" in DEFAULT_UI_CSP
        assert "frame-ancestors 'none'" in DEFAULT_UI_CSP
        assert "sandbox" in DEFAULT_UI_CSP


# ---------------------------------------------------------------------------
# evaluate() -- pure allowlist + CSP (no consent)
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_non_ui_passes_through_unchanged(self) -> None:
        guard = UiResourceGuard()
        decision = guard.evaluate(_NON_UI, _TENANT_A)
        assert decision.is_ui is False
        assert decision.allowed is True
        assert decision.csp is None
        assert decision.requires_consent is False

    def test_ui_not_on_allowlist_denied(self) -> None:
        guard = UiResourceGuard()  # empty default policy
        decision = guard.evaluate(_UI_A, _TENANT_A)
        assert decision.is_ui is True
        assert decision.allowed is False
        assert decision.csp is None

    def test_empty_allowlist_denies_every_ui(self) -> None:
        guard = UiResourceGuard({_TENANT_A: UiResourcePolicy()})
        for uri in (_UI_A, _UI_B, "ui://anything/at/all"):
            assert guard.evaluate(uri, _TENANT_A).allowed is False

    def test_allowlisted_ui_carries_csp_and_requires_consent(self) -> None:
        guard = UiResourceGuard({_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}))})
        decision = guard.evaluate(_UI_A, _TENANT_A)
        assert decision.is_ui is True
        assert decision.allowed is True
        assert decision.csp == DEFAULT_UI_CSP
        assert decision.requires_consent is True


# ---------------------------------------------------------------------------
# enforce() -- full fail-closed enforcement incl. mandatory consent
# ---------------------------------------------------------------------------


class TestEnforce:
    async def test_non_ui_passes_through_and_skips_consent(self) -> None:
        gate = _StubConsentGate(decision=True)
        guard = UiResourceGuard(consent_gate=gate)
        decision = await guard.enforce(_NON_UI, _TENANT_A, _SERVER)
        assert decision.allowed is True
        assert decision.is_ui is False
        assert gate.calls == []  # consent gate never consulted for non-ui://

    async def test_denied_ui_skips_consent(self) -> None:
        gate = _StubConsentGate(decision=True)
        guard = UiResourceGuard(consent_gate=gate)  # empty policy -> denied
        decision = await guard.enforce(_UI_A, _TENANT_A, _SERVER)
        assert decision.allowed is False
        assert gate.calls == []

    async def test_allowlisted_without_consent_gate_is_denied(self) -> None:
        # Mandatory consent, fail-closed: no gate wired -> deny even if allowed.
        guard = UiResourceGuard({_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}))})
        decision = await guard.enforce(_UI_A, _TENANT_A, _SERVER)
        assert decision.allowed is False
        assert "consent" in (decision.reason or "")

    async def test_allowlisted_consent_denied_is_denied(self) -> None:
        gate = _StubConsentGate(decision=False)
        guard = UiResourceGuard(
            {_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}))},
            consent_gate=gate,
        )
        decision = await guard.enforce(_UI_A, _TENANT_A, _SERVER)
        assert decision.allowed is False
        assert len(gate.calls) == 1

    async def test_allowlisted_consent_error_is_denied(self) -> None:
        guard = UiResourceGuard(
            {_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}))},
            consent_gate=_RaisingConsentGate(),
        )
        decision = await guard.enforce(_UI_A, _TENANT_A, _SERVER)
        assert decision.allowed is False
        assert "error" in (decision.reason or "")

    async def test_allowlisted_consent_granted_is_delivered_with_csp(self) -> None:
        gate = _StubConsentGate(decision=True)
        guard = UiResourceGuard(
            {_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}))},
            consent_gate=gate,
        )
        decision = await guard.enforce(_UI_A, _TENANT_A, _SERVER, correlation_id="corr-1")
        assert decision.allowed is True
        assert decision.csp == DEFAULT_UI_CSP
        assert decision.requires_consent is False
        assert gate.calls == [(_UI_A, _TENANT_A, _SERVER, "corr-1")]

    async def test_consent_not_required_delivers_without_gate(self) -> None:
        # A policy that opts out of consent still delivers an allowlisted ui://.
        gate = _StubConsentGate(decision=False)
        guard = UiResourceGuard(
            {_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}), require_consent=False)},
            consent_gate=gate,
        )
        decision = await guard.enforce(_UI_A, _TENANT_A, _SERVER)
        assert decision.allowed is True
        assert decision.csp == DEFAULT_UI_CSP
        assert gate.calls == []  # consent not required -> gate not consulted


# ---------------------------------------------------------------------------
# Per-tenant isolation
# ---------------------------------------------------------------------------


class TestPerTenantIsolation:
    def test_tenant_a_allowlist_does_not_permit_tenant_b(self) -> None:
        guard = UiResourceGuard(
            {
                _TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A})),
                _TENANT_B: UiResourcePolicy(allowlist=frozenset({_UI_B})),
            }
        )
        # tenant A permits its own resource but not tenant B's, and vice versa.
        assert guard.evaluate(_UI_A, _TENANT_A).allowed is True
        assert guard.evaluate(_UI_A, _TENANT_B).allowed is False
        assert guard.evaluate(_UI_B, _TENANT_B).allowed is True
        assert guard.evaluate(_UI_B, _TENANT_A).allowed is False

    def test_unknown_tenant_gets_fail_closed_default(self) -> None:
        guard = UiResourceGuard({_TENANT_A: UiResourcePolicy(allowlist=frozenset({_UI_A}))})
        assert guard.evaluate(_UI_A, "tenant-unknown").allowed is False
        assert guard.evaluate(_UI_A, None).allowed is False

    def test_policy_for_returns_default_for_absent_tenant(self) -> None:
        guard = UiResourceGuard()
        assert guard.policy_for(None).allowlist == frozenset()
        assert guard.policy_for("nope").allowlist == frozenset()
