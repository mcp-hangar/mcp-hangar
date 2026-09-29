"""A tenant-budget refusal taken after the gates is one `batch_call_refused` line (#1629).

The tenant budget refuses in two places: as a gate, when it has no token for
the call, and after every gate has passed, when the slot is taken and the
tenant's slots are all busy (#1445). The first went through `_run_gates`, which
logs, records the span decision and publishes the audit record from one
classification. The second recorded the span decision and the audit record
(#1619) and wrote no log line, so "which calls were refused yesterday" missed
it (ADR-029 s5).

The slot is taken for real here: the tenant's only slot is held by another
call, so the reservation passes the gate and the grant refuses.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from mcp_hangar.domain.events import ToolCallRefused
from mcp_hangar.observability.conventions import Gate
from mcp_hangar.server.tools.batch.tenant_admission import (
    Grant,
    TenantLimits,
    configure_tenant_limits,
    get_tenant_admission,
    reset_tenant_admission,
)
from tests.unit import test_batch_gate_decisions as gate_decisions
from tests.unit import test_refusal_log_carries_no_free_text as gate_cases
from tests.unit.test_batch_gate_precedence import _TENANT, _arrange_catalogue, _run

pytestmark = pytest.mark.otel_sdk

ctx = gate_cases.ctx
exporter = gate_decisions.exporter
_reset_singletons = gate_cases._reset_singletons

#: The only fields a refusal line carries: identifiers and bounded codes (#1581).
_BOUNDED = {
    "event",
    "log_level",
    "call_id",
    "mcp_server",
    "tool",
    "tenant_id",
    "gate",
    "reason",
    "error_type",
    "elapsed_ms",
}


@pytest.fixture
def busy_tenant() -> Iterator[None]:
    """A tenant with one slot, held by another call: its next call passes the gate and is refused its slot."""
    configure_tenant_limits({_TENANT: TenantLimits(max_concurrency=1, rps=100, burst=100)})
    held = get_tenant_admission().admit(_TENANT)
    assert isinstance(held, Grant)
    try:
        yield
    finally:
        held.release()
        reset_tenant_admission()


def _refused_lines(captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in captured if entry["event"] == "batch_call_refused"]


def _audit_records(bus: Any) -> list[ToolCallRefused]:
    return [c.args[0] for c in bus.publish.call_args_list if isinstance(c.args[0], ToolCallRefused)]


class TestALateRefusal:
    def test_is_one_line_naming_the_gate_and_its_bounded_reason(self, ctx, exporter, busy_tenant) -> None:
        _arrange_catalogue()

        with structlog.testing.capture_logs() as captured:
            assert _run().error_type == "TenantQuotaExceeded"

        [line] = _refused_lines(captured)
        assert (line["gate"], line["reason"], line["log_level"]) == ("tenant_budget", "concurrency", "warning")
        assert set(line) <= _BOUNDED, set(line) - _BOUNDED
        assert "error" not in line, "a refusal line carries no free text"

    def test_its_span_line_and_audit_record_agree(self, ctx, exporter, busy_tenant) -> None:
        """One decision feeds all three sinks, so none of them can name another gate or reason."""
        _arrange_catalogue()

        with structlog.testing.capture_logs() as captured:
            _run()

        [line] = _refused_lines(captured)
        [record] = _audit_records(ctx.event_bus)
        denials = [
            (d[Gate.NAME], d.get(Gate.REASON))
            for d in gate_decisions._decisions(exporter)
            if d[Gate.OUTCOME] == Gate.DENY
        ]
        attributes = gate_decisions._call_span(exporter).attributes

        assert denials == [("tenant_budget", "concurrency")]
        assert (line["gate"], line["reason"]) == denials[0]
        assert (record.gate, record.gate_reason) == denials[0]
        assert (attributes[Gate.REFUSAL_GATE], attributes[Gate.REFUSAL_REASON]) == denials[0]


def test_an_in_gate_refusal_is_still_one_line(ctx, exporter) -> None:
    """The gate's own refusal is logged by `_run_gates` and not again after it."""
    _arrange_catalogue()
    configure_tenant_limits({"tenant:other": TenantLimits(max_concurrency=5, rps=1, burst=1)})
    try:
        with structlog.testing.capture_logs() as captured:
            assert _run().error_type == "TenantQuotaExceeded"
    finally:
        reset_tenant_admission()

    [line] = _refused_lines(captured)
    assert (line["gate"], line["reason"]) == ("tenant_budget", "no_budget")
    assert len(_audit_records(ctx.event_bus)) == 1
