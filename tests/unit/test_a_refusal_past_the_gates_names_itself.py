"""A refusal raised past the gates reads like one a gate gave (ADR-029 s12, #1918).

`authorization`, `l7`, `upstream_key` and `rate_limit` are not gates, so
`_GATES` does not enumerate them; `DISPATCH_REFUSALS` does, and a refusal the
executor knows has nothing else to be read by.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.domain.events import ToolCallRefused, ToolInvocationFailed
from mcp_hangar.domain.exceptions import (
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    RateLimitExceeded,
    UpstreamPeerKeyMismatchError,
)
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server.cli.services.refusal_log import read_refusals
from mcp_hangar.server.tools.batch.executor import _REFUSED_AT_DISPATCH
from mcp_hangar.server.tools.batch.refusal_shape import (
    AUTHORIZATION_DENIAL_KEY,
    DISPATCH_REFUSALS,
    TOOL_FAILURE_KEY,
    shape_refusal,
)
from tests.unit import test_batch_gate_decisions as gate_decisions
from tests.unit.test_batch_gate_precedence import _run

ctx = gate_decisions.ctx
exporter = gate_decisions.exporter
_reset_singletons = gate_decisions._reset_singletons

KEY = "sha256:" + "a" * 64

REFUSALS = [
    (EgressPolicyDeniedError("s", "t", "nope"), "l7", "deny"),
    (EgressPolicyApprovalRequiredError("s", "t"), "l7", "require_approval"),
    (UpstreamPeerKeyMismatchError(KEY), "upstream_key", "upstream_peer_key_mismatch"),
    (RateLimitExceeded(limit=1, retry_after=2.0, scope="caller", key="k"), "rate_limit", "exceeded"),
]


def _refused_events(ctx: Any) -> list[ToolCallRefused]:
    published = [c.args[0] for c in ctx.event_bus.publish.call_args_list]
    return [event for event in published if isinstance(event, ToolCallRefused)]


def test_every_refusal_at_dispatch_is_in_the_table_this_test_runs() -> None:
    assert {type(error).__name__ for error, _gate, _reason in REFUSALS} == set(_REFUSED_AT_DISPATCH)
    assert {name: (gate, reason) for name, (gate, reason) in DISPATCH_REFUSALS.items()} == {
        type(error).__name__: (gate, reason) for error, gate, reason in REFUSALS
    }


@pytest.mark.parametrize(("error", "gate", "reason"), REFUSALS, ids=lambda v: getattr(type(v), "__name__", ""))
def test_a_refusal_at_dispatch_tells_its_caller_who_refused_and_keeps_the_message_below(
    ctx, exporter, error, gate, reason
) -> None:
    gate_decisions._arrange_catalogue()
    ctx.command_bus.send.side_effect = error

    result = _run()

    first, below = result.error.split("\n", 1)
    assert first.startswith(f"Refused by MCP Hangar ({gate}): {reason}. ")
    assert below == str(error)
    failure = result.refusal_meta[TOOL_FAILURE_KEY]
    assert failure["code"] == f"{gate}.{reason}"
    [event] = _refused_events(ctx)
    assert failure["correlationId"] == event.event_id


def test_a_rate_limit_is_retryable_from_the_time_it_clears(ctx, exporter) -> None:
    gate_decisions._arrange_catalogue()
    ctx.command_bus.send.side_effect = RateLimitExceeded(limit=1, retry_after=2.0, scope="caller", key="k")

    failure = _run().refusal_meta[TOOL_FAILURE_KEY]

    assert failure["class"] == "reliability" and failure["retryable"] is True
    assert isinstance(failure["retryNotBefore"], int)


def test_a_rate_limit_and_a_key_mismatch_are_recorded_under_their_own_names(ctx, exporter) -> None:
    gate_decisions._arrange_catalogue()
    ctx.command_bus.send.side_effect = RateLimitExceeded(limit=1, retry_after=2.0, scope="caller", key="k")
    _run()
    [limited] = _refused_events(ctx)
    assert (limited.gate, limited.gate_reason, limited.l7_verdict) == ("rate_limit", "exceeded", None)

    ctx.event_bus.publish.reset_mock()
    error = UpstreamPeerKeyMismatchError(KEY)
    error.details["correlation_id"] = "the-failed-invocation"
    ctx.command_bus.send.side_effect = error
    _run()
    [mismatch] = _refused_events(ctx)
    assert (mismatch.gate, mismatch.observed_peer_spki) == ("upstream_key", KEY)
    assert mismatch.correlation_id == "the-failed-invocation"


def test_an_ordinary_failure_at_dispatch_stays_unshaped(ctx, exporter) -> None:
    gate_decisions._arrange_catalogue()
    ctx.command_bus.send.side_effect = RuntimeError("boom")

    result = _run()

    assert result.error == "boom" and result.refusal_meta is None
    assert _refused_events(ctx) == []


@pytest.mark.parametrize("reason", ["tool_invoke_denied", "unauthenticated"])
def test_a_denied_tool_invoke_carries_the_undisclosed_envelope(reason) -> None:
    meta = shape_refusal("authorization", reason, server="s", event_id="e").meta
    assert meta[AUTHORIZATION_DENIAL_KEY] == {"reason": "insufficient_authorization", "remediation": "undisclosed"}
    assert meta[TOOL_FAILURE_KEY]["class"] == "governance"


class _Exporter:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def export_tool_invocation(self, *, status: str, **_fields: Any) -> None:
        self.statuses.append(status)


def test_a_key_mismatch_at_dispatch_is_exported_once() -> None:
    audit = _Exporter()
    handler = OTLPAuditEventHandler(audit_exporter=audit)

    handler.handle(ToolInvocationFailed(mcp_server_id="s", tool_name="t", failure_code="upstream_peer_key_mismatch"))
    handler.handle(ToolCallRefused(mcp_server_id="s", tool_name="t", gate="upstream_key"))
    handler.handle(ToolCallRefused(mcp_server_id="s", tool_name="t", gate="cold_start"))

    assert audit.statuses == ["error", "denied"]


def test_why_shows_a_key_mismatch_at_dispatch_once(tmp_path) -> None:
    db = tmp_path / "events.db"
    SQLiteEventStore(str(db)).append_at_end(
        "calls",
        [
            ToolInvocationFailed(
                mcp_server_id="s",
                tool_name="older",
                correlation_id="old",
                failure_code="upstream_peer_key_mismatch",
            ),
            ToolInvocationFailed(
                mcp_server_id="s",
                tool_name="t",
                correlation_id="c",
                failure_code="upstream_peer_key_mismatch",
            ),
            ToolCallRefused(
                mcp_server_id="s",
                tool_name="t",
                correlation_id="c",
                gate="upstream_key",
                gate_reason="upstream_peer_key_mismatch",
            ),
        ],
    )

    assert [(r.event_type, r.tool, r.gate) for r in read_refusals(db)] == [
        ("ToolCallRefused", "t", "upstream_key"),
        ("ToolInvocationFailed", "older", "upstream_tls"),
    ]
