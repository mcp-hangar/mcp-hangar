"""A metric defined but never registered is invisible (#1059).

`CollectorRegistry.collect()` walks only what was registered, so a collector
that `_register_all_metrics()` forgets accumulates in process memory and never
reaches a scrape. From outside that is indistinguishable from a feature nobody
built -- and it is worse than an absent metric when the docs promise a query
against it.

Four had been forgotten: the three approval-gate counters (dead since 2.10.0,
with three PromQL queries in `guides/OBSERVABILITY.md` that could never return
a row) and the Audit-mode egress observation counter, which is the signal
ADR-013 calls the safe adoption path for an egress policy.

This test is the fix. The named cases below would each have caught one; the
sweep catches the next one, which is the point.
"""

from __future__ import annotations

import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.metrics import Counter, Gauge, Histogram, REGISTRY


def _module_level_metrics() -> dict[str, Counter | Gauge | Histogram]:
    return {
        name: value
        for name, value in vars(prometheus_metrics).items()
        if isinstance(value, (Counter, Gauge, Histogram))
    }


def test_every_module_level_metric_is_registered() -> None:
    """Walks the module rather than naming metrics, so the next one is covered too."""
    registered = {collector.name for collector in REGISTRY._collectors.values()}

    unregistered = sorted(
        f"{name} ({metric.name})" for name, metric in _module_level_metrics().items() if metric.name not in registered
    )

    assert not unregistered, (
        "defined but never registered, so absent from /metrics: "
        + ", ".join(unregistered)
        + " -- add them to _register_all_metrics()"
    )


def test_the_sweep_actually_sees_the_metrics() -> None:
    """A guard that walked an empty set would pass forever."""
    assert len(_module_level_metrics()) > 50


@pytest.mark.parametrize(
    "metric_name",
    [
        "mcp_hangar_approval_requests",
        "mcp_hangar_approval_deliveries",
        "mcp_hangar_approval_decisions",
        "mcp_hangar_egress_policy_violations_observed",
    ],
)
def test_a_previously_dead_metric_reaches_the_exposition(metric_name: str) -> None:
    """The four from #1059, named so a revert is loud about which one it broke."""
    assert REGISTRY.get(metric_name) is not None


def test_an_incremented_approval_counter_is_scrapable() -> None:
    """End to end through the exposition, not just the registry index."""
    prometheus_metrics.APPROVAL_DECISIONS_TOTAL.inc(channel="slack", decision="granted")

    assert "mcp_hangar_approval_decisions_total" in prometheus_metrics.get_metrics()


def _metrics_nothing_ever_writes_to() -> list[str]:
    """Module-level metrics referenced nowhere but their own definition.

    Registration was only half the question (#1059). A metric that is
    registered and never incremented is exposed as a TYPE header with no
    sample, which reads to an operator, a dashboard and a docs table exactly
    like a metric that is working and quiet -- `mcp_hangar_http_retries_total`
    shipped a Grafana panel that could never draw a line (#1163).

    The scan skips the registration list, which references every metric by
    construction, and treats a reference from anywhere else -- including the
    `record_*` helpers in `metrics.py` itself -- as an emitter.
    """
    import ast
    import re
    from pathlib import Path

    names = set(_module_level_metrics()) | {
        name for name, value in vars(prometheus_metrics).items() if isinstance(value, prometheus_metrics.Info)
    }
    src = Path(prometheus_metrics.__file__).parent
    written_to: set[str] = set()

    module = ast.parse(Path(prometheus_metrics.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == "_register_all_metrics":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load) and inner.id in names:
                written_to.add(inner.id)

    for path in src.rglob("*.py"):
        if path.name == "metrics.py":
            continue
        text = path.read_text(encoding="utf-8")
        written_to |= {name for name in names if re.search(rf"\b{name}\b", text)}

    return sorted(names - written_to)


def test_every_registered_metric_has_something_that_writes_to_it() -> None:
    assert _metrics_nothing_ever_writes_to() == [], (
        "registered but never written to, so /metrics carries a TYPE header and no sample: "
        + ", ".join(_metrics_nothing_ever_writes_to())
        + " -- emit it, or delete it and whatever promises it"
    )
