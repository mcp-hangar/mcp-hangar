"""L7 verdict observer contract for the domain layer (#1295, ADR-029 s7).

The aggregate applies an L7 egress policy verdict inside `invoke_tool`, and
that verdict never left the domain except as an exception or a persisted event.
This port hands it out, once per call, so an adapter can put it on a span
without the domain depending on OpenTelemetry and without evaluating the policy
a second time.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class L7VerdictKind(StrEnum):
    """What the aggregate did with the policy's decision (ADR-029 s5)."""

    ALLOW = "allow"
    #: Audit mode saw a deny or require-approval decision and let the call through.
    AUDIT_OBSERVED = "audit_observed"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    #: A require-approval decision that a granted approval converted.
    APPROVAL_HONORED = "approval_honored"


@dataclass(frozen=True)
class L7Verdict:
    """One applied verdict, in the domain's own spelling.

    ``mode`` is the `PolicyMode` value (``Audit``/``Enforce``) and ``rule_kind``
    the `Decision.rule_kind` (``tool``, ``header``, ``arguments``). An adapter
    maps them to its own vocabulary. Nothing here is free text: the decision's
    reasons stay in the domain event and the domain log line.
    """

    verdict: L7VerdictKind
    mode: str
    rule_kind: str
    policy_id: str | None
    #: The arguments could not be inspected, so the decision is a fail-closed deny.
    inspection_failed: bool = False


class L7VerdictObserver(ABC):
    """Contract for observing the L7 verdict the aggregate applied."""

    @abstractmethod
    def observe(self, verdict: L7Verdict) -> None:
        """Record *verdict*. Called before the aggregate raises, if it raises."""


class NullL7VerdictObserver(L7VerdictObserver):
    """Null object: observes nothing."""

    def observe(self, verdict: L7Verdict) -> None:
        """No-op implementation."""


# The observer the aggregate uses. The Null object until the composition root
# installs the adapter, so importing the domain never reaches for one. Read at
# call time rather than at construction, so a server built before bootstrap
# still reports.
_default_l7_verdict_observer: L7VerdictObserver = NullL7VerdictObserver()


def set_default_l7_verdict_observer(observer: L7VerdictObserver) -> None:
    """Install the observer the aggregate reports to. Called from bootstrap."""
    global _default_l7_verdict_observer
    _default_l7_verdict_observer = observer


def get_default_l7_verdict_observer() -> L7VerdictObserver:
    """The observer the aggregate reports to."""
    return _default_l7_verdict_observer
