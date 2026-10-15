"""`hangar_metrics` reports what the counters hold (#1668).

Per-server `invocations`, `errors` and `avg_latency_ms` were always 0, a tool's
`errors` too, `count` was whichever histogram sample came last, and
`discovery.*.cycles` held a Unix timestamp. The rules matched substrings of metric
names that no metric has. These tests drive the real metric functions and read
the aggregation back.
"""

from __future__ import annotations

import pytest

from mcp_hangar import metrics as m
from mcp_hangar.server.tools import health

SERVER = "metrics-probe"
SOURCE = "probe-source"


def _fresh() -> dict:
    return {
        "mcp_servers": {SERVER: {"invocations": 0, "errors": 0, "avg_latency_ms": 0}},
        "tool_calls": {},
        "discovery": {},
        "errors": {},
    }


def _drop(store, needle) -> None:
    """Delete the entries whose key mentions *needle*, keeping the container's type."""
    for key in [k for k in store if needle in k]:
        del store[key]


@pytest.fixture(autouse=True)
def _isolated():
    def wipe():
        _drop(m.TOOL_CALLS_TOTAL._values, SERVER)
        _drop(m.TOOL_CALLS_TOTAL._values, "someone-else")
        _drop(m.TOOL_CALL_ERRORS_TOTAL._values, SERVER)
        for store in (
            m.TOOL_CALL_DURATION_SECONDS._counts,
            m.TOOL_CALL_DURATION_SECONDS._sums,
            m.TOOL_CALL_DURATION_SECONDS._buckets,
        ):
            _drop(store, SERVER)
        _drop(m.DISCOVERY_CYCLES_TOTAL._values, SOURCE)
        m.DISCOVERY_LAST_CYCLE_TIMESTAMP.remove(source_type=SOURCE)
        m.DISCOVERY_PROVIDERS_TOTAL.remove(source_type=SOURCE)
        _drop(m.ERRORS_TOTAL._values, "probe_error")

    wipe()
    yield
    wipe()


def test_after_n_calls_the_json_reports_n_and_the_error_count():
    for _ in range(3):
        m.observe_tool_call(SERVER, "add", duration=0.010, success=True)
    for _ in range(2):
        m.observe_tool_call(SERVER, "add", duration=0.0, success=False, error_type="timeout")
    m.observe_tool_call(SERVER, "sub", duration=0.030, success=True)
    result = _fresh()

    health._aggregate_tool_calls(result)

    assert result["tool_calls"][f"{SERVER}.add"] == {"count": 5, "errors": 2}
    assert result["tool_calls"][f"{SERVER}.sub"] == {"count": 1, "errors": 0}
    assert result["mcp_servers"][SERVER]["invocations"] == 6
    assert result["mcp_servers"][SERVER]["errors"] == 2


def test_average_latency_is_the_mean_of_the_successful_calls():
    m.observe_tool_call(SERVER, "add", duration=0.010, success=True)
    m.observe_tool_call(SERVER, "add", duration=0.030, success=True)
    result = _fresh()

    health._aggregate_tool_calls(result)

    assert result["mcp_servers"][SERVER]["avg_latency_ms"] == pytest.approx(20.0)


def test_a_server_that_is_not_in_the_repository_does_not_break_the_aggregation():
    m.observe_tool_call("someone-else", "add", duration=0.01, success=True)
    result = _fresh()

    health._aggregate_tool_calls(result)

    assert result["mcp_servers"][SERVER]["invocations"] == 0


def test_discovery_cycles_are_a_count_not_a_timestamp():
    for _ in range(4):
        m.record_discovery_cycle(SOURCE, duration=0.1, discovered=3, registered=2, quarantined=1)
    result = _fresh()

    health._aggregate_discovery(result)

    assert result["discovery"][SOURCE] == {
        "cycles": 4,
        "mcp_servers_discovered": 3,
        "mcp_servers_registered": 2,
        "mcp_servers_quarantined": 1,
    }


def test_component_errors_are_summed_by_type():
    m.ERRORS_TOTAL.inc(2, component="tool", error_type="probe_error")
    m.ERRORS_TOTAL.inc(1, component="gc", error_type="probe_error")
    result = _fresh()

    health._aggregate_errors(result)

    assert result["errors"]["probe_error"] == 3


def test_the_registered_tool_reports_the_counters(monkeypatch):
    """The tool itself, registered on a stub server, with one real McpServer in the repository."""
    import sys
    from types import SimpleNamespace

    from mcp_hangar.domain.model.mcp_server import McpServer

    server = McpServer(mcp_server_id=SERVER, mode="subprocess", command=[sys.executable, "-c", "pass"])
    ctx = SimpleNamespace(repository=SimpleNamespace(get_all=lambda: {SERVER: server}), groups={})
    monkeypatch.setattr(health, "get_context", lambda: ctx)

    registered = {}

    class _Stub:
        def tool(self, name=None, **_):
            def decorate(fn):
                registered[name] = fn
                return fn

            return decorate

    health.register_health_tools(_Stub())
    for _ in range(2):
        m.observe_tool_call(SERVER, "add", duration=0.02, success=True)
    m.observe_tool_call(SERVER, "add", duration=0.0, success=False, error_type="timeout")

    result = registered["hangar_metrics"]()

    assert result["mcp_servers"][SERVER]["invocations"] == 3
    assert result["mcp_servers"][SERVER]["errors"] == 1
    assert result["tool_calls"][f"{SERVER}.add"] == {"count": 3, "errors": 1}
    assert result["summary"]["total_tool_calls"] >= 3
