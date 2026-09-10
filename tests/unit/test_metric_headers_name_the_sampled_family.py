"""`# HELP` and `# TYPE` name the family the samples carry (#1260).

In text format 0.0.4 the family a `# TYPE` line names is the one its samples
are named after -- exactly, or plus the suffixes the type defines: `_bucket`,
`_sum` and `_count` for a histogram, `_sum` and `_count` for a summary. The
registry appended `_total` to counter samples and `_info` to info samples but
not to their header lines, so Prometheus filed every counter's type and help
text under a name with no samples, and `/api/v1/metadata` answered nothing for
the series anyone queries. `rate()` kept working, which is why nobody noticed.
"""

from __future__ import annotations

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.metrics import CollectorRegistry, Counter, Gauge, Histogram, Info, Summary

_SAMPLE_SUFFIXES = {
    "counter": ("",),
    "gauge": ("",),
    "histogram": ("_bucket", "_sum", "_count"),
    "summary": ("_sum", "_count"),
}


def _misnamed(exposition: str) -> list[str]:
    """Every header or sample that does not belong to the family declared above it."""
    help_family = type_family = kind = ""
    problems: list[str] = []
    for line in exposition.splitlines():
        if not line:
            continue
        if line.startswith("# HELP "):
            help_family = line.split(" ", 3)[2]
        elif line.startswith("# TYPE "):
            _, _, type_family, kind = line.split(" ", 3)
            if type_family != help_family:
                problems.append(f"# HELP {help_family} is followed by # TYPE {type_family}")
        else:
            sample = line.split("{", 1)[0].split(" ", 1)[0]
            if sample not in {type_family + suffix for suffix in _SAMPLE_SUFFIXES[kind]}:
                problems.append(f"# TYPE {type_family} {kind} is followed by a sample named {sample}")
    return problems


def test_every_kind_of_metric_names_the_family_it_samples() -> None:
    registry = CollectorRegistry()
    counter = Counter("toy_calls", "calls", labels=["tool"])
    gauge = Gauge("toy_inflight", "in flight")
    histogram = Histogram("toy_duration_seconds", "duration")
    summary = Summary("toy_size_bytes", "size")
    info = Info("toy_build", "build")
    for collector in (counter, gauge, histogram, summary, info):
        registry.register(collector)
    counter.inc(tool="boom")
    gauge.set(2)
    histogram.observe(0.3)
    summary.observe(10)
    info.info(version="1.0")

    exposition = registry.collect()

    assert _misnamed(exposition) == [], exposition
    assert "# TYPE toy_calls_total counter" in exposition
    assert "# TYPE toy_build_info gauge" in exposition


def test_the_served_exposition_has_no_misnamed_family() -> None:
    """Every registered metric, including the headers of those with no sample yet.

    The counter from the issue gets a sample first, so its header lines are
    checked against a real sample line and not only against each other.
    """
    prometheus_metrics.TOOL_CALLS_TOTAL.inc(mcp_server="notes", tool="boom", status="error")

    exposition = prometheus_metrics.get_metrics()

    assert _misnamed(exposition) == []
    assert "# TYPE mcp_hangar_tool_calls_total counter" in exposition.splitlines()
