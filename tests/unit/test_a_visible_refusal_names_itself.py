"""A refusal of a tool the caller may see says who refused it and what to do (ADR-029 s12, #1918)."""

from __future__ import annotations

from mcp_hangar.server.tools.batch.executor import _GATES, format_result_dict
from mcp_hangar.server.tools.batch.models import CallResult
from mcp_hangar.server.tools.batch.refusal_shape import (
    AUTHORIZATION_DENIAL_KEY,
    GATE_DISPOSITIONS,
    TOOL_FAILURE_KEY,
    Disposition,
    disposition,
    shape_refusal,
)


def test_every_stage_of_the_gates_has_decided_what_its_caller_is_told() -> None:
    stages = {gate.__name__.removeprefix("_gate_") for gate in _GATES}
    assert stages == set(GATE_DISPOSITIONS)


def test_a_tool_the_caller_may_not_see_is_not_shaped() -> None:
    for gate in ("tool_access", "withdrawal", "resolve_target"):
        assert disposition(gate, "any") == Disposition.HIDDEN


def test_a_cold_start_is_shaped_only_for_a_refusal_on_purpose() -> None:
    assert disposition("cold_start", "upstream_peer_key_mismatch") == Disposition.SHAPED
    assert disposition("cold_start", "not_revived_by_calls") == Disposition.SHAPED
    assert disposition("cold_start", "start_failed") == Disposition.NOT_A_REFUSAL


def test_the_text_names_hangar_the_gate_the_reason_and_the_next_step() -> None:
    shape = shape_refusal("digest_pin", "digest_mismatch", server="github", event_id="e1")
    assert shape.text == (
        "Refused by MCP Hangar (digest_pin): digest_mismatch. "
        "Review the change, then run: mcp-hangar pin --write --server github"
    )


def test_a_governance_refusal_is_not_retryable_and_names_its_event() -> None:
    failure = shape_refusal("validators", "validator_denied", server="s", event_id="e1").meta[TOOL_FAILURE_KEY]
    assert failure == {"class": "governance", "code": "validators.validator_denied", "correlationId": "e1"}


def test_a_refusal_that_clears_by_waiting_is_reliability_with_a_time_when_known() -> None:
    known = shape_refusal("circuit_breaker", "circuit_open", server="s", event_id="e", retry_after_s=5.0)
    failure = known.meta[TOOL_FAILURE_KEY]
    assert failure["class"] == "reliability" and failure["retryable"] is True
    assert isinstance(failure["retryNotBefore"], int)

    unknown = shape_refusal("tenant_budget", "rate", server="s", event_id="e").meta[TOOL_FAILURE_KEY]
    assert unknown["class"] == "reliability" and "retryNotBefore" not in unknown


def test_a_refusal_with_no_bounded_reason_invents_none() -> None:
    shape = shape_refusal("validators", None, server="s", event_id="e1")
    assert shape.text.startswith("Refused by MCP Hangar (validators). ")
    assert shape.meta[TOOL_FAILURE_KEY]["code"] == "validators"


def test_only_an_approval_denied_carries_the_authorization_envelope() -> None:
    denied = shape_refusal("approval", "approval_denied", server="s", event_id="e").meta
    assert denied[AUTHORIZATION_DENIAL_KEY] == {"reason": "insufficient_authorization", "remediation": "unavailable"}
    assert AUTHORIZATION_DENIAL_KEY not in shape_refusal("approval", "approval_timeout", server="s", event_id="e").meta


def test_a_batch_entry_carries_the_fields_and_an_ordinary_one_does_not() -> None:
    meta = {TOOL_FAILURE_KEY: {"class": "governance"}}
    refused = CallResult(index=0, call_id="c", success=False, error="x", refusal_meta=meta)
    assert format_result_dict(refused)["_meta"] == meta
    assert "_meta" not in format_result_dict(CallResult(index=0, call_id="c", success=True))
