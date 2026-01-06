"""Tests for server tools - health module helper functions."""

from mcp_hangar.server.tools.health import (
    _collect_samples_from_collector,
    _process_discovery_metric,
    _process_error_metric,
    _process_invocations_metric,
    _process_metric_sample,
    _process_tool_calls_metric,
)


class MockSample:
    """Mock sample for testing."""

    def __init__(self, name: str, labels: dict, value: float):
        self.name = name
        self.labels = labels
        self.value = value


class MockCollector:
    """Mock collector for testing."""

    def __init__(self, samples):
        self._samples = samples

    def collect(self):
        return self._samples


class TestCollectSamplesFromCollector:
    """Tests for _collect_samples_from_collector function."""

    def test_returns_empty_list_when_no_collect_method(self):
        """Should return empty list when collector has no collect method."""

        class NoCollect:
            pass

        result = _collect_samples_from_collector(NoCollect())
        assert result == []

    def test_returns_list_when_collect_returns_list(self):
        """Should return list directly when collect returns list."""
        samples = [MockSample("test", {}, 1.0)]
        collector = MockCollector(samples)

        result = _collect_samples_from_collector(collector)
        assert result == samples

    def test_handles_tuple_with_lists(self):
        """Should handle tuple containing lists."""
        samples = [MockSample("test", {}, 1.0)]
        collector = MockCollector((samples, []))

        result = _collect_samples_from_collector(collector)
        assert len(result) == 1

    def test_handles_tuple_with_samples(self):
        """Should handle tuple containing sample objects."""
        sample = MockSample("test", {}, 1.0)
        collector = MockCollector((sample,))

        result = _collect_samples_from_collector(collector)
        assert len(result) == 1


class TestProcessToolCallsMetric:
    """Tests for _process_tool_calls_metric function."""

    def test_ignores_non_tool_calls_metric(self):
        """Should ignore metrics that don't contain 'tool_calls'."""
        tool_calls = {}
        _process_tool_calls_metric("some_metric", {}, 1.0, tool_calls)
        assert tool_calls == {}

    def test_processes_tool_calls_count(self):
        """Should process tool_calls count metric."""
        tool_calls = {}
        _process_tool_calls_metric(
            "tool_calls_total", {"provider": "test-provider", "tool": "test-tool"}, 5.0, tool_calls
        )

        assert "test-provider.test-tool" in tool_calls
        assert tool_calls["test-provider.test-tool"]["count"] == 5

    def test_processes_tool_calls_error(self):
        """Should process tool_calls error metric."""
        tool_calls = {}
        _process_tool_calls_metric(
            "tool_calls_error_total", {"provider": "test-provider", "tool": "test-tool"}, 2.0, tool_calls
        )

        assert "test-provider.test-tool" in tool_calls
        assert tool_calls["test-provider.test-tool"]["errors"] == 2

    def test_uses_unknown_for_missing_labels(self):
        """Should use 'unknown' for missing labels."""
        tool_calls = {}
        _process_tool_calls_metric("tool_calls_total", {}, 1.0, tool_calls)

        assert "unknown.unknown" in tool_calls


class TestProcessInvocationsMetric:
    """Tests for _process_invocations_metric function."""

    def test_ignores_non_invocations_metric(self):
        """Should ignore metrics that don't contain 'invocations'."""
        providers = {"test": {"invocations": 0}}
        _process_invocations_metric("some_metric", {"provider": "test"}, 1.0, providers)
        assert providers["test"]["invocations"] == 0

    def test_ignores_metric_without_provider_label(self):
        """Should ignore metrics without provider label."""
        providers = {"test": {"invocations": 0}}
        _process_invocations_metric("invocations_total", {}, 1.0, providers)
        assert providers["test"]["invocations"] == 0

    def test_processes_invocations_metric(self):
        """Should process invocations metric."""
        providers = {"test-provider": {"invocations": 0}}
        _process_invocations_metric("invocations_total", {"provider": "test-provider"}, 10.0, providers)

        assert providers["test-provider"]["invocations"] == 10


class TestProcessDiscoveryMetric:
    """Tests for _process_discovery_metric function."""

    def test_ignores_non_discovery_metric(self):
        """Should ignore metrics that don't contain 'discovery'."""
        discovery = {}
        _process_discovery_metric("some_metric", {}, 1.0, discovery)
        assert discovery == {}

    def test_processes_discovery_cycle_metric(self):
        """Should process discovery cycle metric."""
        discovery = {}
        _process_discovery_metric("discovery_cycle_total", {"source_type": "kubernetes"}, 5.0, discovery)

        assert "kubernetes" in discovery
        assert discovery["kubernetes"]["cycles"] == 5

    def test_processes_discovery_providers_metric(self):
        """Should process discovery providers metric."""
        discovery = {}
        _process_discovery_metric(
            "discovery_providers_total", {"source_type": "docker", "status": "registered"}, 3.0, discovery
        )

        assert "docker" in discovery
        assert discovery["docker"]["providers_registered"] == 3


class TestProcessErrorMetric:
    """Tests for _process_error_metric function."""

    def test_ignores_non_error_metric(self):
        """Should ignore metrics that don't contain 'error'."""
        errors = {}
        _process_error_metric("some_metric", {}, 1.0, errors)
        assert errors == {}

    def test_processes_error_metric(self):
        """Should process error metric."""
        errors = {}
        _process_error_metric("errors_total", {"error_type": "timeout"}, 3.0, errors)

        assert "timeout" in errors
        assert errors["timeout"] == 3

    def test_accumulates_errors(self):
        """Should accumulate errors of same type."""
        errors = {"timeout": 2}
        _process_error_metric("errors_total", {"error_type": "timeout"}, 3.0, errors)

        assert errors["timeout"] == 5

    def test_uses_metric_name_when_no_error_type_label(self):
        """Should use metric name when error_type label is missing."""
        errors = {}
        _process_error_metric("my_error_metric", {}, 1.0, errors)

        assert "my_error_metric" in errors


class TestProcessMetricSample:
    """Tests for _process_metric_sample function."""

    def test_ignores_sample_without_labels(self):
        """Should ignore sample without labels attribute."""

        class NoLabels:
            value = 1.0

        result = {"tool_calls": {}, "providers": {}, "discovery": {}, "errors": {}}
        _process_metric_sample(NoLabels(), result)

        assert result["tool_calls"] == {}

    def test_ignores_sample_without_value(self):
        """Should ignore sample without value attribute."""

        class NoValue:
            labels = {}

        result = {"tool_calls": {}, "providers": {}, "discovery": {}, "errors": {}}
        _process_metric_sample(NoValue(), result)

        assert result["tool_calls"] == {}

    def test_processes_complete_sample(self):
        """Should process sample with all required attributes."""
        sample = MockSample("tool_calls_total", {"provider": "test", "tool": "my_tool"}, 5.0)
        result = {"tool_calls": {}, "providers": {}, "discovery": {}, "errors": {}}

        _process_metric_sample(sample, result)

        assert "test.my_tool" in result["tool_calls"]
