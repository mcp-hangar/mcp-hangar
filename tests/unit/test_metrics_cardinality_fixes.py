"""Regression tests for observability-audit metric fixes (Phase 2)."""

from __future__ import annotations

import re

from mcp_hangar import metrics as m


def _hist_count(server: str, tool: str) -> float:
    """Parse the tool_call_duration_seconds histogram _count for one labelset."""
    out = m.get_metrics()
    pat = re.compile(
        r'mcp_hangar_tool_call_duration_seconds_count\{[^}]*mcp_server="'
        + re.escape(server)
        + r'"[^}]*tool="'
        + re.escape(tool)
        + r'"[^}]*\}\s+([0-9.]+)'
    )
    mobj = pat.search(out)
    return float(mobj.group(1)) if mobj else 0.0


def test_failed_call_does_not_observe_latency_histogram() -> None:
    # Unique labels avoid cross-test contamination.
    server, tool = "srv-fail-iso", "tool-fail-iso"
    before = _hist_count(server, tool)
    m.observe_tool_call(server, tool, duration=0.0, success=False, error_type="timeout")
    assert _hist_count(server, tool) == before  # histogram untouched by the failure


def test_successful_call_observes_latency_histogram() -> None:
    server, tool = "srv-ok-iso", "tool-ok-iso"
    before = _hist_count(server, tool)
    m.observe_tool_call(server, tool, duration=1.25, success=True)
    assert _hist_count(server, tool) == before + 1


def test_events_compacted_has_no_stream_id_label() -> None:
    m.record_events_compacted("stream-" + "x" * 40, 3)
    out = m.get_metrics()
    assert "mcp_hangar_events_compacted_total" in out
    assert "stream_id" not in out
