"""Regression tests for the Prometheus exposition format of the main registry.

The custom ``CollectorRegistry`` appends ``_total`` to counter names on
exposition, so counter ``name=`` literals must declare the base name only.
"""

from mcp_hangar import metrics


def _collect() -> str:
    # Increment so the counters emit a series line (zero-value counters do not).
    metrics.COST_CENTS_TOTAL.labels(mcp_server="s1", tool="t1", cost_model="token").inc(5)
    metrics.COST_ATTRIBUTIONS_TOTAL.labels(mcp_server="s1", tool="t1").inc(1)
    return metrics.REGISTRY.collect()


def test_cost_counters_render_single_total_suffix() -> None:
    output = _collect()

    assert "mcp_hangar_cost_cents_total{" in output
    assert "mcp_hangar_cost_attributions_total{" in output


def test_cost_counters_have_no_doubled_total_suffix() -> None:
    output = _collect()

    assert "mcp_hangar_cost_cents_total_total" not in output
    assert "mcp_hangar_cost_attributions_total_total" not in output
