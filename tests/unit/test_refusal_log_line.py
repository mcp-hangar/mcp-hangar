"""Every refused call says so in the log, once, with the reason the span carries.

After #1285 a refusal was legible in a trace -- `hangar.gate.outcome=deny`,
`hangar.refusal.gate`, `hangar.refusal.reason` -- and still not in the log.
`_log_call_failure` called a call `refused` for two L7 error types only, and it
runs on the invoke path, which a gate refusal never reaches. Each gate wrote its
own line instead, under its own name (`tool_withdrawn_rejected`,
`tool_digest_pin_rejected`) and at its own level, so "which calls were refused
yesterday" had no single query (ADR-029 s5).
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import pytest
import structlog

from mcp_hangar.observability.conventions import Gate
from mcp_hangar.server.tools.batch import BatchExecutor
from mcp_hangar.server.tools.batch.executor import _GATES, _REFUSED_AT_DISPATCH
from mcp_hangar.server.tools.batch.models import CallResult
from tests.unit import test_batch_gate_decisions as gate_decisions
from tests.unit.test_batch_gate_precedence import _run

pytestmark = pytest.mark.otel_sdk

ctx = gate_decisions.ctx
exporter = gate_decisions.exporter
#: Autouse in the module it comes from, so it has to be re-exported here too:
#: without it one test's arrangement (a denied tool access) survives into the next.
_reset_singletons = gate_decisions._reset_singletons

#: Every gate that refuses on its own, and the reason it gives (#1285's table).
_REFUSALS = gate_decisions._REFUSALS


def _lines(captured: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in captured if entry["event"] == event]


class TestARefusedCall:
    @pytest.mark.parametrize("gate", sorted(_REFUSALS))
    def test_is_one_warning_naming_the_gate_and_its_reason(self, ctx, exporter, gate) -> None:
        with structlog.testing.capture_logs() as captured:
            with ExitStack() as stack:
                gate_decisions._arrange(ctx, gate, stack)
                assert _run().success is False

        [line] = _lines(captured, "batch_call_refused")
        assert (line["gate"], line["reason"]) == (gate, _REFUSALS[gate])
        assert line["log_level"] == "warning"
        assert (line["tool"], line["call_id"]) == (gate_decisions._TOOL, "c-1")
        assert not _lines(captured, "batch_call_failed"), "a refusal is not a failure"

    @pytest.mark.parametrize("gate", sorted(_REFUSALS))
    def test_says_what_the_span_says(self, ctx, exporter, gate) -> None:
        """One computation feeds both, so a reader comparing them is never puzzled."""
        with structlog.testing.capture_logs() as captured:
            with ExitStack() as stack:
                gate_decisions._arrange(ctx, gate, stack)
                _run()

        [line] = _lines(captured, "batch_call_refused")
        attributes = gate_decisions._call_span(exporter).attributes
        assert line["gate"] == attributes[Gate.REFUSAL_GATE]
        assert line["reason"] == attributes[Gate.REFUSAL_REASON]

    def test_the_tenant_budget_names_which_budget_refused_it(self, ctx, exporter) -> None:
        """Its reason is the admission's own bounded code, not the refusal's error_type.

        Arranged as #1285's own tenant-budget case is: a limit configured for
        another tenant leaves this one with no budget at all.
        """
        from mcp_hangar.server.tools.batch.tenant_admission import (
            TenantLimits,
            configure_tenant_limits,
            reset_tenant_admission,
        )

        gate_decisions._arrange_catalogue()
        configure_tenant_limits({"tenant:other": TenantLimits(max_concurrency=5, rps=1, burst=1)})
        try:
            with structlog.testing.capture_logs() as captured:
                assert _run().error_type == "TenantQuotaExceeded"
        finally:
            reset_tenant_admission()

        [line] = _lines(captured, "batch_call_refused")
        assert (line["gate"], line["reason"]) == ("tenant_budget", "no_budget")


class TestWhatIsNotARefusal:
    def test_a_gate_that_broke_is_a_failure_at_debug(self, ctx, exporter) -> None:
        """A cold start that did not start is `error`: the machinery broke."""
        gate_decisions._arrange_catalogue()
        ctx.get_mcp_server.return_value.state.value = "cold"
        ctx.command_bus.send.side_effect = RuntimeError("boom")

        with structlog.testing.capture_logs() as captured:
            assert _run().error_type == "McpServerStartError"

        assert not _lines(captured, "batch_call_refused")
        [line] = _lines(captured, "batch_call_failed")
        assert (line["gate"], line["reason"], line["log_level"]) == ("cold_start", "start_failed", "debug")

    def test_an_upstream_failure_is_not_logged_as_refused(self, ctx, exporter) -> None:
        gate_decisions._arrange_catalogue()
        failed = CallResult(index=0, call_id="c-1", success=False, error="x", error_type="RuntimeError", elapsed_ms=0)

        with structlog.testing.capture_logs() as captured:
            with patch.object(BatchExecutor, "_invoke_with_retry", return_value=failed):
                assert _run().success is False

        assert not _lines(captured, "batch_call_refused")

    def test_a_call_that_passes_every_gate_logs_neither(self, ctx, exporter) -> None:
        gate_decisions._arrange_catalogue()

        with structlog.testing.capture_logs() as captured:
            assert _run().success is True

        assert not _lines(captured, "batch_call_refused")
        assert not _lines(captured, "batch_call_failed")


class TestTheGatesNoLongerRestateThemselves:
    """The per-gate lines stay for debugging, below the level a deployment emits."""

    @pytest.mark.parametrize(
        "gate, event",
        [("withdrawal", "tool_withdrawn_rejected"), ("tool_access", "tool_access_denied")],
    )
    def test_the_old_line_is_now_debug(self, ctx, exporter, gate, event) -> None:
        with structlog.testing.capture_logs() as captured:
            with ExitStack() as stack:
                gate_decisions._arrange(ctx, gate, stack)
                _run()

        [line] = _lines(captured, event)
        assert line["log_level"] == "debug"


def test_the_invoke_path_reads_the_same_refusal_set() -> None:
    """`_log_call_failure` had its own copy of the two L7 names; now there is one set.

    A refusal raised past the gates (an L7 denial, a spent rate-limit budget)
    reaches the invoke path instead of a gate, and `batch_call_refused` is what
    it must log there too.
    """
    import inspect

    from mcp_hangar.server.tools.batch import executor

    source = inspect.getsource(executor._log_call_failure)
    assert "_REFUSED_AT_DISPATCH" in source
    assert "EgressPolicyDeniedError" not in source, "no second copy of the set"
    assert "EgressPolicyDeniedError" in _REFUSED_AT_DISPATCH


def test_every_gate_can_be_named_in_a_refusal_line() -> None:
    """The log's `gate` uses the same derivation as `hangar.gate.name` (#1285)."""
    names = [gate.__name__.removeprefix("_gate_") for gate in _GATES]

    assert set(_REFUSALS) <= set(names)
    assert len(names) == len(set(names))
