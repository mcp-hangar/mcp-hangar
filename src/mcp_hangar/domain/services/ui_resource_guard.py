"""Fail-closed guard for ``ui://`` (MCP Apps / SEP-1865) resources.

MCP Apps lets a tool return a ``ui://`` resource that a client renders in a
webview / sandboxed iframe -- an XSS / data-exfiltration surface. This guard is
the enforcement point that decides whether a ``ui://`` resource may be
delivered, and under what constraints. It enforces three fail-closed controls:

1. **Per-tenant allowlist.** A ``ui://`` resource is DENIED unless it matches
   the tenant's :class:`~mcp_hangar.domain.value_objects.ui_resource.UiResourcePolicy`
   allowlist. **The default policy has an empty allowlist, so every ``ui://``
   resource is denied by default.** An unknown tenant likewise gets the empty
   default -> denied.
2. **Restrictive CSP.** An allowed ``ui://`` resource carries a restrictive
   Content-Security-Policy (:data:`~mcp_hangar.domain.value_objects.ui_resource.DEFAULT_UI_CSP`)
   in its decision, to be attached to the delivered resource's metadata.
3. **Mandatory consent.** An allowed ``ui://`` resource additionally requires a
   consent decision from the existing approval gate before delivery. No consent
   gate wired, or consent not granted (denied / error / timeout) -> DENIED.

Non-``ui://`` resources are completely unaffected: :meth:`UiResourceGuard.evaluate`
returns a pass-through decision and :meth:`UiResourceGuard.enforce` never invokes
the consent gate for them.

Dormancy note (be honest): Hangar does not relay ``ui://`` resources today --
there is no ``resources/read`` proxy path in ``src/``. This guard is the
ready-but-dormant enforcement point. When a ``ui://`` resource relay is added,
call :meth:`UiResourceGuard.enforce` on each resource before it is delivered to
the client; that is the single wiring hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mcp_hangar.domain.value_objects.ui_resource import (
    UiResourcePolicy,
    is_ui_scheme,
)
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class UiResourceDecision:
    """Outcome of evaluating a single resource against the guard.

    Attributes:
        uri: The resource URI that was evaluated.
        is_ui: Whether the URI targets the ``ui://`` scheme.
        allowed: Whether the resource may be delivered. For non-``ui://``
            resources this is always True (pass-through). For ``ui://``
            resources it is True only when allowlisted AND (if required) consent
            was granted.
        csp: The Content-Security-Policy to attach to the delivered resource, or
            None when not applicable (non-``ui://`` or denied).
        requires_consent: Whether an allowlisted ``ui://`` resource still needs a
            consent decision before delivery. Set on the pure
            :meth:`UiResourceGuard.evaluate` result; cleared once consent has
            been resolved by :meth:`UiResourceGuard.enforce`.
        reason: Human-readable explanation, primarily for denials / audit.
    """

    uri: str
    is_ui: bool
    allowed: bool
    csp: str | None = None
    requires_consent: bool = False
    reason: str | None = None


@runtime_checkable
class UiConsentGate(Protocol):
    """Consent provider for ``ui://`` delivery.

    Implemented in production by an adapter over the existing
    ``ApprovalGateService`` (see :class:`ApprovalConsentGate`). Returns True only
    when a human has affirmatively consented to delivering the ``ui://``
    resource; returns False on denial, timeout, or error (fail-closed).
    """

    async def request_consent(
        self,
        uri: str,
        tenant_id: str | None,
        mcp_server_id: str,
        correlation_id: str,
    ) -> bool:
        """Request consent to deliver a ``ui://`` resource. Fail-closed."""
        ...


class UiResourceGuard:
    """Fail-closed enforcement point for ``ui://`` resources.

    Holds per-tenant :class:`UiResourcePolicy` objects plus a default policy used
    for tenants with no explicit policy (empty allowlist -> deny all ``ui://``).
    Optionally holds a :class:`UiConsentGate` used to mandate consent on allowed
    ``ui://`` resources.

    ``evaluate`` is a pure, synchronous allowlist + CSP decision (no consent).
    ``enforce`` is the full async enforcement: allowlist + CSP + mandatory
    consent. Both leave non-``ui://`` resources untouched.
    """

    def __init__(
        self,
        policies: dict[str, UiResourcePolicy] | None = None,
        *,
        default_policy: UiResourcePolicy | None = None,
        consent_gate: UiConsentGate | None = None,
    ) -> None:
        # Per-tenant policies. Absent tenant -> default_policy (fail-closed).
        self._policies: dict[str, UiResourcePolicy] = dict(policies or {})
        # The default is an empty-allowlist policy: deny all ui:// unless a
        # tenant explicitly allowlists a resource.
        self._default_policy = default_policy or UiResourcePolicy()
        self._consent_gate = consent_gate

    def policy_for(self, tenant_id: str | None) -> UiResourcePolicy:
        """Return the effective policy for ``tenant_id`` (fail-closed default).

        Unknown or None tenant -> the empty-allowlist default policy, so every
        ``ui://`` resource is denied.
        """
        if tenant_id is None:
            return self._default_policy
        return self._policies.get(tenant_id, self._default_policy)

    def evaluate(self, uri: str, tenant_id: str | None) -> UiResourceDecision:
        """Pure allowlist + CSP decision for a single resource (no consent).

        - Non-``ui://`` resource -> pass through unchanged (allowed, no CSP).
        - ``ui://`` resource not on the tenant allowlist -> denied (fail-closed).
        - ``ui://`` resource on the allowlist -> allowed here, carrying the CSP
          and flagged ``requires_consent`` per policy. Final delivery still
          requires :meth:`enforce` to resolve consent.
        """
        if not is_ui_scheme(uri):
            return UiResourceDecision(
                uri=uri,
                is_ui=False,
                allowed=True,
                reason="non-ui-scheme: not governed by the ui:// guard",
            )

        policy = self.policy_for(tenant_id)
        if not policy.is_allowed(uri):
            return UiResourceDecision(
                uri=uri,
                is_ui=True,
                allowed=False,
                reason="ui:// resource not on the tenant allowlist (fail-closed)",
            )

        return UiResourceDecision(
            uri=uri,
            is_ui=True,
            allowed=True,
            csp=policy.csp,
            requires_consent=policy.require_consent,
            reason="ui:// resource allowlisted; consent required before delivery"
            if policy.require_consent
            else "ui:// resource allowlisted",
        )

    async def enforce(
        self,
        uri: str,
        tenant_id: str | None,
        mcp_server_id: str,
        correlation_id: str = "",
    ) -> UiResourceDecision:
        """Full fail-closed enforcement for a single resource before delivery.

        Runs :meth:`evaluate`, then -- for an allowlisted ``ui://`` resource that
        requires consent -- mandates consent via the wired :class:`UiConsentGate`.

        Fail-closed on every consent edge:
        - allowlisted + requires consent but **no consent gate wired** -> DENIED.
        - consent gate returns False (denied / timeout) -> DENIED.
        - consent gate raises -> DENIED (error is swallowed, not propagated).

        Non-``ui://`` resources and denied ``ui://`` resources return the
        ``evaluate`` decision unchanged (the consent gate is never consulted).
        """
        decision = self.evaluate(uri, tenant_id)

        if not decision.is_ui or not decision.allowed:
            return decision

        if not decision.requires_consent:
            return decision

        if self._consent_gate is None:
            logger.warning(
                "ui_resource_consent_gate_missing",
                uri=uri,
                tenant_id=tenant_id,
                mcp_server_id=mcp_server_id,
            )
            return UiResourceDecision(
                uri=uri,
                is_ui=True,
                allowed=False,
                reason="ui:// consent mandated but no consent gate wired (fail-closed)",
            )

        try:
            consented = await self._consent_gate.request_consent(
                uri=uri,
                tenant_id=tenant_id,
                mcp_server_id=mcp_server_id,
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 -- fail-closed: any error denies delivery
            logger.warning(
                "ui_resource_consent_gate_error",
                uri=uri,
                tenant_id=tenant_id,
                mcp_server_id=mcp_server_id,
                exc_info=True,
            )
            return UiResourceDecision(
                uri=uri,
                is_ui=True,
                allowed=False,
                reason="ui:// consent gate error (fail-closed)",
            )

        if not consented:
            return UiResourceDecision(
                uri=uri,
                is_ui=True,
                allowed=False,
                reason="ui:// resource consent not granted (fail-closed)",
            )

        # Consent granted: deliver with CSP; consent is now resolved.
        return UiResourceDecision(
            uri=uri,
            is_ui=True,
            allowed=True,
            csp=decision.csp,
            requires_consent=False,
            reason="ui:// resource allowlisted and consent granted",
        )
