"""Cold-start metrics have to actually reach the registry.

`mcp_hangar_mcp_server_cold_start_seconds` is described in `metrics.py` as "the
critical UX metric" -- time from a user's request to a ready backend. The
aggregate publishes it through the `IMetricsPublisher` port, the Prometheus
adapter behind that port exists, and nothing ever connected the two:
`PrometheusMetricsPublisher` appeared exactly once in the codebase, at its own
`class` statement. Never imported, never constructed, never exported.

So every `McpServer` fell back to `NullMetricsPublisher`, and the histogram and
its in-progress gauge were never observed in production. A dashboard panel on
that metric had been reading an empty series.

These tests assert the wiring rather than the port: a server built the way
`server/config.py` builds one must publish, and the explicit-injection path must
keep overriding. Asserting on the registry rather than on a mock is the point --
a mock would have passed against the broken wiring too.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_hangar.domain.contracts.metrics_publisher import (
    IMetricsPublisher,
    NullMetricsPublisher,
    get_default_metrics_publisher,
    set_default_metrics_publisher,
)
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.infrastructure.metrics_publisher import PrometheusMetricsPublisher
from mcp_hangar.server.bootstrap.observability import init_metrics_publisher
from mcp_hangar.metrics import PROVIDER_COLD_START_IN_PROGRESS, PROVIDER_COLD_START_SECONDS


@pytest.fixture(autouse=True)
def _restore_default():
    previous = get_default_metrics_publisher()
    yield
    set_default_metrics_publisher(previous)


def _server(mcp_server_id: str = "demo", **kwargs) -> McpServer:
    """A server built the way the config loader builds one."""
    return McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["true"], **kwargs)


def _histogram_count(mcp_server: str) -> float:
    """Observation count for one server. collect() yields (buckets, sums, counts)."""
    _buckets, _sums, counts = PROVIDER_COLD_START_SECONDS.collect()
    return sum(s.value for s in counts if s.labels.get("mcp_server") == mcp_server)


def _gauge_value(mcp_server: str) -> float | None:
    for sample in PROVIDER_COLD_START_IN_PROGRESS.collect():
        if sample.labels.get("mcp_server") == mcp_server:
            return sample.value
    return None


class TestTheDefaultIsTheRealPublisher:
    def test_the_domain_default_is_the_null_object_before_wiring(self):
        """The domain must not reach for an adapter on import -- that is the leak."""
        set_default_metrics_publisher(NullMetricsPublisher())
        assert isinstance(get_default_metrics_publisher(), NullMetricsPublisher)

    def test_the_composition_root_installs_the_prometheus_adapter(self):
        """The step that was missing entirely: nothing ever constructed it."""
        set_default_metrics_publisher(NullMetricsPublisher())
        init_metrics_publisher()
        assert isinstance(get_default_metrics_publisher(), PrometheusMetricsPublisher)

    def test_a_server_built_like_production_picks_it_up(self):
        init_metrics_publisher()
        assert not isinstance(_server()._metrics_publisher, NullMetricsPublisher)

    def test_a_cold_start_reaches_the_registry(self):
        """Asserted against the real registry: a mock passes even when unwired."""
        init_metrics_publisher()
        before = _histogram_count("metrics-wiring-probe")
        server = _server(mcp_server_id="metrics-wiring-probe")
        server._end_cold_start_tracking(server._begin_cold_start_tracking(), True)
        assert _histogram_count("metrics-wiring-probe") == before + 1

    def test_the_in_progress_gauge_returns_to_zero(self):
        init_metrics_publisher()
        server = _server(mcp_server_id="metrics-gauge-probe")
        start = server._begin_cold_start_tracking()
        during = _gauge_value("metrics-gauge-probe")
        server._end_cold_start_tracking(start, True)
        assert during == 1
        assert _gauge_value("metrics-gauge-probe") == 0


class TestInjectionStillWins:
    def test_an_explicit_publisher_overrides_the_default(self):
        explicit = Mock(spec=IMetricsPublisher)
        assert _server(metrics_publisher=explicit)._metrics_publisher is explicit

    def test_the_default_can_be_swapped(self):
        replacement = Mock(spec=IMetricsPublisher)
        set_default_metrics_publisher(replacement)
        assert _server()._metrics_publisher is replacement

    def test_a_null_publisher_is_still_honoured_when_asked_for(self):
        """Opting out explicitly must remain possible; it just is not the default."""
        null = NullMetricsPublisher()
        assert _server(metrics_publisher=null)._metrics_publisher is null


class TestConnectionStateGoesThroughThePort:
    """`set_connection_active` was imported straight from `metrics` in three places.

    That is the aggregate reaching past its own port for an adapter -- the
    `domain.model.mcp_server -> metrics` line in the import-contract debt
    ledger. Routing it through the publisher removes the edge and makes the call
    observable to a test without patching a module.
    """

    def test_the_port_declares_it(self):
        assert hasattr(IMetricsPublisher, "set_connection_active")

    def test_the_null_object_implements_it(self):
        NullMetricsPublisher().set_connection_active("demo", True)  # must not raise

    def test_the_aggregate_reports_disconnection_through_the_port(self):
        publisher = Mock(spec=IMetricsPublisher)
        server = _server(metrics_publisher=publisher)
        server._client = Mock()
        server.shutdown()
        publisher.set_connection_active.assert_called_with(server.mcp_server_id, False)
