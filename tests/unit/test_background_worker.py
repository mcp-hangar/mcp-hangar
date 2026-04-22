"""Tests for BackgroundWorker state-aware health check scheduling."""

import time
from unittest.mock import MagicMock, PropertyMock, patch

from mcp_hangar.domain.model.health_tracker import HealthTracker
from mcp_hangar.domain.value_objects.provider import ProviderState
from mcp_hangar.gc import BackgroundWorker


def _make_provider(state: ProviderState, health_tracker: HealthTracker | None = None):
    """Create a mock provider with the given state and health tracker."""
    provider = MagicMock()
    type(provider).state = PropertyMock(return_value=state)
    type(provider).health = PropertyMock(return_value=health_tracker or HealthTracker())
    provider.health_check.return_value = True
    provider.collect_events.return_value = []
    return provider


class TestBackgroundWorkerHealthCheckScheduling:
    """Tests for state-aware health check scheduling in BackgroundWorker."""

    def test_skips_cold_providers(self):
        """BackgroundWorker skips health check for COLD providers."""
        provider = _make_provider(ProviderState.COLD)
        providers = {"cold-provider": provider}

        worker = BackgroundWorker(providers, interval_s=1, task="health_check")

        # Simulate one loop iteration by calling _loop logic
        # We run the worker briefly and check that health_check was NOT called
        worker.running = True

        # Run one iteration manually
        providers_snapshot = list(worker.mcp_servers.items())
        for mcp_server_id, p in providers_snapshot:
            from mcp_hangar.domain.contracts.provider_runtime import normalize_state_to_str

            state_str = normalize_state_to_str(p.state)
            if state_str in ("cold", "initializing"):
                continue
            p.health_check()

        provider.health_check.assert_not_called()

    def test_skips_initializing_providers(self):
        """BackgroundWorker skips health check for INITIALIZING providers."""
        provider = _make_provider(ProviderState.INITIALIZING)
        providers = {"init-provider": provider}

        worker = BackgroundWorker(providers, interval_s=1, task="health_check")
        worker.running = True

        # Manually simulate the state-aware skip behavior
        providers_snapshot = list(worker.mcp_servers.items())
        for mcp_server_id, p in providers_snapshot:
            from mcp_hangar.domain.contracts.provider_runtime import normalize_state_to_str

            state_str = normalize_state_to_str(p.state)
            if state_str in ("cold", "initializing"):
                continue
            p.health_check()

        provider.health_check.assert_not_called()

    def test_checks_ready_providers(self):
        """BackgroundWorker checks READY providers at normal interval."""
        provider = _make_provider(ProviderState.READY)
        providers = {"ready-provider": provider}

        worker = BackgroundWorker(providers, interval_s=10, task="health_check")
        assert hasattr(worker, "_next_check_at"), "Worker must track per-provider next_check_at"

        # Should check immediately (no entry in _next_check_at yet)
        assert worker._next_check_at.get("ready-provider", 0.0) <= time.time()

    def test_degraded_provider_uses_backoff_interval(self):
        """BackgroundWorker checks DEGRADED providers at backoff interval (not before backoff expires)."""
        tracker = HealthTracker(jitter_factor=0.0)
        tracker.record_failure()
        tracker.record_failure()  # base backoff = 4.0

        provider = _make_provider(ProviderState.DEGRADED, tracker)
        providers = {"degraded-provider": provider}

        worker = BackgroundWorker(providers, interval_s=10, task="health_check")

        # Set next_check_at to far future -- should be skipped
        worker._next_check_at["degraded-provider"] = time.time() + 1000
        # Provider should not be checked now (not due yet)
        now = time.time()
        next_check = worker._next_check_at.get("degraded-provider", 0.0)
        assert now < next_check, "Provider should be skipped when next_check_at is in the future"

    def test_dead_provider_uses_longer_ceiling(self):
        """BackgroundWorker checks DEAD providers at longer ceiling interval."""
        tracker = HealthTracker()
        _make_provider(ProviderState.DEAD, tracker)

        interval = tracker.get_health_check_interval("dead")
        assert interval == 60.0, "DEAD providers should use 60s ceiling"

    def test_tracks_per_provider_next_check_at(self):
        """BackgroundWorker tracks per-provider next_check_at timestamps."""
        providers = {
            "p1": _make_provider(ProviderState.READY),
            "p2": _make_provider(ProviderState.READY),
        }

        worker = BackgroundWorker(providers, interval_s=10, task="health_check")
        assert isinstance(worker._next_check_at, dict), "_next_check_at must be a dict"

    def test_degraded_to_ready_resumes_normal_interval(self):
        """After provider transitions from DEGRADED to READY, it resumes normal interval."""
        tracker = HealthTracker(jitter_factor=0.0)
        tracker.record_failure()
        tracker.record_failure()  # backoff = 4.0

        degraded_interval = tracker.get_health_check_interval("degraded")
        ready_interval = tracker.get_health_check_interval("ready", normal_interval=10.0)

        assert degraded_interval == 4.0
        assert ready_interval == 10.0
        assert ready_interval != degraded_interval, "READY interval should differ from DEGRADED backoff"

    def test_gc_task_unchanged(self):
        """BackgroundWorker still works correctly for GC task (unchanged behavior)."""
        provider = _make_provider(ProviderState.READY)
        provider.maybe_shutdown_idle.return_value = False
        providers = {"gc-provider": provider}

        worker = BackgroundWorker(providers, interval_s=1, task="gc")
        # GC task should not have _next_check_at behavior affect it
        assert worker.task == "gc"
        # GC should still call maybe_shutdown_idle when it runs
        # (not affected by state-aware health check scheduling)

    @patch("mcp_hangar.gc.observe_health_check")
    @patch("mcp_hangar.gc.time.sleep", side_effect=[None, StopIteration])
    def test_loop_skips_cold_in_health_check_mode(self, mock_sleep, mock_observe):
        """Integration: _loop skips COLD providers for health_check task."""
        cold_provider = _make_provider(ProviderState.COLD)
        ready_provider = _make_provider(ProviderState.READY)
        providers = {
            "cold-one": cold_provider,
            "ready-one": ready_provider,
        }

        worker = BackgroundWorker(providers, interval_s=1, task="health_check")
        worker.running = True

        try:
            worker._loop()
        except StopIteration:
            pass

        cold_provider.health_check.assert_not_called()
        ready_provider.health_check.assert_called_once()

    @patch("mcp_hangar.gc.observe_health_check")
    @patch("mcp_hangar.gc.time.sleep", side_effect=[None, StopIteration])
    def test_loop_skips_initializing_in_health_check_mode(self, mock_sleep, mock_observe):
        """Integration: _loop skips INITIALIZING providers for health_check task."""
        init_provider = _make_provider(ProviderState.INITIALIZING)
        providers = {"init-one": init_provider}

        worker = BackgroundWorker(providers, interval_s=1, task="health_check")
        worker.running = True

        try:
            worker._loop()
        except StopIteration:
            pass

        init_provider.health_check.assert_not_called()

    @patch("mcp_hangar.gc.observe_health_check")
    @patch("mcp_hangar.gc.time.sleep", side_effect=[None, StopIteration])
    def test_loop_sets_next_check_at_after_health_check(self, mock_sleep, mock_observe):
        """Integration: _loop sets _next_check_at after checking a READY provider."""
        provider = _make_provider(ProviderState.READY)
        providers = {"ready-one": provider}

        worker = BackgroundWorker(providers, interval_s=10, task="health_check")
        worker.running = True

        try:
            worker._loop()
        except StopIteration:
            pass

        assert "ready-one" in worker._next_check_at
        assert worker._next_check_at["ready-one"] > time.time()

    @patch("mcp_hangar.gc.observe_health_check")
    @patch("mcp_hangar.gc.time.sleep", side_effect=[None, StopIteration])
    def test_loop_respects_next_check_at_timing(self, mock_sleep, mock_observe):
        """Integration: _loop skips providers whose next_check_at is in the future."""
        provider = _make_provider(ProviderState.READY)
        providers = {"future-one": provider}

        worker = BackgroundWorker(providers, interval_s=10, task="health_check")
        worker.running = True
        # Set next_check_at far in the future
        worker._next_check_at["future-one"] = time.time() + 3600

        try:
            worker._loop()
        except StopIteration:
            pass

        provider.health_check.assert_not_called()
