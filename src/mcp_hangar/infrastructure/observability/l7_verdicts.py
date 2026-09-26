"""The L7 verdict the aggregate applied, carried to the call's span and log line (#1295).

The aggregate reports through `L7VerdictObserver`; the adapter here writes the
report into a holder the batch executor binds for one attempt. The executor
reads the holder once after dispatch, and that one reading feeds both
`batch.call.<tool>` and `batch_call_refused`, so neither classifies the call
for itself and nothing evaluates the policy a second time.

Only bounded values leave: the verdict, the lowercased mode, the rule kind
(``arguments`` is exported as ``argument``, ADR-029 s5; the domain value and
the persisted event keep ``arguments``) and a `policy_id` of the content-hash
shape. `bounded_error_type` cannot check the id: it rejects the colon.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ...domain.contracts.l7_verdict_observer import L7Verdict, L7VerdictKind, L7VerdictObserver
from ...logging_config import get_logger
from ...observability.conventions import L7

logger = get_logger(__name__)


class L7VerdictHolder:
    """What the aggregate decided on one attempt, or None when no policy was evaluated."""

    __slots__ = ("verdict",)

    def __init__(self) -> None:
        self.verdict: L7Verdict | None = None


_holder: ContextVar[L7VerdictHolder | None] = ContextVar("l7_verdict_holder", default=None)


@contextmanager
def holding_l7_verdict(holder: L7VerdictHolder) -> Iterator[L7VerdictHolder]:
    """Bind *holder* for the dispatch inside, which runs the aggregate on this thread."""
    token = _holder.set(holder)
    try:
        yield holder
    finally:
        _holder.reset(token)


class ContextL7VerdictObserver(L7VerdictObserver):
    """The adapter: fill the bound holder. A call nobody bound one for is not recorded."""

    def observe(self, verdict: L7Verdict) -> None:
        holder = _holder.get()
        if holder is not None:
            holder.verdict = verdict


_MODES = frozenset({"audit", "enforce"})
_RULE_KINDS = {"tool": "tool", "header": "header", "arguments": "argument"}
_POLICY_ID = re.compile(r"sha256:[0-9a-f]{1,64}")


@dataclass(frozen=True)
class L7Decision:
    """A verdict reduced to the values the telemetry contract allows; None where one is not allowed."""

    verdict: str
    mode: str | None
    rule_kind: str | None
    policy_id: str | None
    inspection_failed: bool

    @property
    def evaluator_failed(self) -> bool:
        """Refused because the arguments could not be inspected: the evaluator broke (ADR-029 s5)."""
        return self.inspection_failed and self.verdict == L7VerdictKind.DENY

    def log_fields(self) -> dict[str, Any]:
        """The fields `batch_call_refused` carries for an L7 refusal, in place of the policy's reasons."""
        return {
            "l7_verdict": self.verdict,
            "l7_mode": self.mode,
            "l7_rule_kind": self.rule_kind,
            "l7_inspection_failed": self.inspection_failed,
        }


def bounded_l7_decision(verdict: L7Verdict | None) -> L7Decision | None:
    """*verdict* as bounded values, or None when there is none."""
    if verdict is None:
        return None
    mode = verdict.mode.lower()
    policy_id = verdict.policy_id
    return L7Decision(
        verdict=L7VerdictKind(verdict.verdict).value,
        mode=mode if mode in _MODES else None,
        rule_kind=_RULE_KINDS.get(verdict.rule_kind),
        policy_id=policy_id if policy_id is not None and _POLICY_ID.fullmatch(policy_id) else None,
        inspection_failed=verdict.inspection_failed,
    )


def record_l7_decision(decision: L7Decision) -> None:
    """Set the `hangar.l7.*` attributes on the ambient `batch.call.<tool>` span. Never raises."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if not span.get_span_context().is_valid:
            return
        span.set_attribute(L7.VERDICT, decision.verdict)
        optional = ((L7.MODE, decision.mode), (L7.RULE_KIND, decision.rule_kind), (L7.POLICY_ID, decision.policy_id))
        for key, value in optional:
            if value is not None:
                span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 -- fault barrier: telemetry must not break a call
        logger.debug("l7_verdict_record_failed")
