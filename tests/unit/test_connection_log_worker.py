"""Tests for ConnectionLogWorker -- background connection monitoring.

Tests cover:
- Worker lifecycle (start, stop, idempotent start)
- LEARNING-mode-only polling (skips DISABLED, ENFORCING)
- Docker/K8s monitor delegation based on provider mode
- Profiler observation recording
- Per-provider fault barrier
- Monitor init failure (ImportError) handling
- Empty provider list no-op cycle
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation
from mcp_hangar.domain.value_objects.provider import ProviderMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(mode: ProviderMode) -> Mock:
    """Create a mock provider with a given mode."""
    provider = Mock()
    provider.mode = mode
    provider._mode = mode
    return provider


def _make_observation(provider_id: str = "test-provider") -> NetworkObservation:
    return NetworkObservation(
        timestamp=time.time(),
        provider_id=provider_id,
        destination_host="10.0.0.1",
        destination_port=443,
        protocol="tcp",
        direction="outbound",
    )


def _make_profiler(mode: BehavioralMode = BehavioralMode.LEARNING) -> Mock:
    profiler = Mock()
    profiler.get_mode = Mock(return_value=mode)
    profiler.record_observation = Mock()
    return profiler


def _make_docker_monitor(observations: list[NetworkObservation] | None = None) -> Mock:
    monitor = Mock()
    monitor.poll_connections = Mock(return_value=observations or [])
    return monitor


def _make_k8s_monitor(observations: list[NetworkObservation] | None = None) -> Mock:
    monitor = Mock()
    monitor.poll_connections = Mock(return_value=observations or [])
    return monitor


# ---------------------------------------------------------------------------
# Test: Worker lifecycle
# ---------------------------------------------------------------------------


class TestWorkerLifecycle:
    """Tests for start/stop lifecycle behavior."""

    def test_start_creates_and_starts_daemon_thread(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler()
        providers: dict = {}
        worker = ConnectionLogWorker(
            providers=providers,
            profiler=profiler,
            interval_s=1,
        )

        assert worker.running is False
        worker.start()
        assert worker.running is True
        assert worker.thread.is_alive()
        assert worker.thread.daemon is True
        worker.stop()
        # Give thread time to exit
        time.sleep(0.1)

    def test_start_is_idempotent(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler()
        providers: dict = {}
        worker = ConnectionLogWorker(
            providers=providers,
            profiler=profiler,
            interval_s=1,
        )

        worker.start()
        thread_1 = worker.thread
        # Second start should be no-op
        worker.start()
        assert worker.thread is thread_1
        worker.stop()
        time.sleep(0.1)

    def test_stop_sets_running_false(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler()
        providers: dict = {}
        worker = ConnectionLogWorker(
            providers=providers,
            profiler=profiler,
            interval_s=1,
        )

        worker.start()
        assert worker.running is True
        worker.stop()
        assert worker.running is False
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# Test: LEARNING mode filtering
# ---------------------------------------------------------------------------


class TestModeFiltering:
    """Tests for provider mode filtering (only LEARNING is polled)."""

    def test_skips_disabled_mode_provider(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler(BehavioralMode.DISABLED)
        provider = _make_provider(ProviderMode.DOCKER)
        providers = {"disabled-prov": provider}

        docker_monitor = _make_docker_monitor()

        worker = ConnectionLogWorker(
            providers=providers,
            profiler=profiler,
            interval_s=60,
        )
        worker._docker_monitor = docker_monitor
        worker._poll_provider("disabled-prov", provider)

        docker_monitor.poll_connections.assert_not_called()
        profiler.record_observation.assert_not_called()

    def test_skips_enforcing_mode_provider(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler(BehavioralMode.ENFORCING)
        provider = _make_provider(ProviderMode.DOCKER)
        providers = {"enforcing-prov": provider}

        docker_monitor = _make_docker_monitor()

        worker = ConnectionLogWorker(
            providers=providers,
            profiler=profiler,
            interval_s=60,
        )
        worker._docker_monitor = docker_monitor
        worker._poll_provider("enforcing-prov", provider)

        docker_monitor.poll_connections.assert_not_called()
        profiler.record_observation.assert_not_called()

    def test_polls_learning_mode_docker_provider(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        obs = _make_observation("learning-prov")
        profiler = _make_profiler(BehavioralMode.LEARNING)
        provider = _make_provider(ProviderMode.DOCKER)
        providers = {"learning-prov": provider}

        docker_monitor = _make_docker_monitor([obs])

        worker = ConnectionLogWorker(
            providers=providers,
            profiler=profiler,
            interval_s=60,
        )
        worker._docker_monitor = docker_monitor
        worker._poll_provider("learning-prov", provider)

        docker_monitor.poll_connections.assert_called_once_with("learning-prov")
        profiler.record_observation.assert_called_once_with(obs)


# ---------------------------------------------------------------------------
# Test: Monitor delegation by provider mode
# ---------------------------------------------------------------------------


class TestMonitorDelegation:
    """Tests for correct monitor selection based on provider mode."""

    def test_docker_provider_uses_docker_monitor(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        obs = _make_observation("docker-prov")
        profiler = _make_profiler(BehavioralMode.LEARNING)
        provider = _make_provider(ProviderMode.DOCKER)
        docker_monitor = _make_docker_monitor([obs])

        worker = ConnectionLogWorker(
            providers={"docker-prov": provider},
            profiler=profiler,
            interval_s=60,
        )
        worker._docker_monitor = docker_monitor
        worker._poll_provider("docker-prov", provider)

        docker_monitor.poll_connections.assert_called_once_with("docker-prov")

    def test_remote_provider_uses_k8s_monitor(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        obs = _make_observation("remote-prov")
        profiler = _make_profiler(BehavioralMode.LEARNING)
        provider = _make_provider(ProviderMode.REMOTE)
        k8s_monitor = _make_k8s_monitor([obs])

        worker = ConnectionLogWorker(
            providers={"remote-prov": provider},
            profiler=profiler,
            interval_s=60,
            k8s_namespace="mcp-ns",
        )
        worker._k8s_monitor = k8s_monitor
        worker._poll_provider("remote-prov", provider)

        k8s_monitor.poll_connections.assert_called_once_with("remote-prov", namespace="mcp-ns")
        profiler.record_observation.assert_called_once_with(obs)

    def test_subprocess_provider_skipped_no_monitor(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler(BehavioralMode.LEARNING)
        provider = _make_provider(ProviderMode.SUBPROCESS)

        worker = ConnectionLogWorker(
            providers={"sub-prov": provider},
            profiler=profiler,
            interval_s=60,
        )
        worker._docker_monitor = _make_docker_monitor()
        worker._k8s_monitor = _make_k8s_monitor()
        worker._poll_provider("sub-prov", provider)

        # No monitor used for subprocess mode
        worker._docker_monitor.poll_connections.assert_not_called()
        worker._k8s_monitor.poll_connections.assert_not_called()
        profiler.record_observation.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Profiler observation recording
# ---------------------------------------------------------------------------


class TestObservationRecording:
    """Tests for correct observation forwarding to profiler."""

    def test_records_each_observation(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        obs1 = _make_observation("prov")
        obs2 = NetworkObservation(
            timestamp=time.time(),
            provider_id="prov",
            destination_host="10.0.0.2",
            destination_port=8080,
            protocol="tcp",
            direction="outbound",
        )
        profiler = _make_profiler(BehavioralMode.LEARNING)
        provider = _make_provider(ProviderMode.DOCKER)
        docker_monitor = _make_docker_monitor([obs1, obs2])

        worker = ConnectionLogWorker(
            providers={"prov": provider},
            profiler=profiler,
            interval_s=60,
        )
        worker._docker_monitor = docker_monitor
        worker._poll_provider("prov", provider)

        assert profiler.record_observation.call_count == 2
        profiler.record_observation.assert_any_call(obs1)
        profiler.record_observation.assert_any_call(obs2)


# ---------------------------------------------------------------------------
# Test: Per-provider fault barrier
# ---------------------------------------------------------------------------


class TestFaultBarrier:
    """Tests for per-provider exception isolation."""

    def test_exception_in_one_provider_does_not_stop_others(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        obs = _make_observation("good-prov")
        profiler = _make_profiler(BehavioralMode.LEARNING)

        bad_provider = _make_provider(ProviderMode.DOCKER)
        good_provider = _make_provider(ProviderMode.DOCKER)

        bad_monitor = Mock()
        bad_monitor.poll_connections = Mock(side_effect=RuntimeError("container gone"))

        good_monitor = _make_docker_monitor([obs])

        worker = ConnectionLogWorker(
            providers={"bad-prov": bad_provider, "good-prov": good_provider},
            profiler=profiler,
            interval_s=60,
        )

        # We simulate the loop manually using _poll_provider.
        # The fault barrier is in _poll_provider itself.
        worker._docker_monitor = bad_monitor
        # Should not raise
        worker._poll_provider("bad-prov", bad_provider)

        # Now switch to good monitor for good provider
        worker._docker_monitor = good_monitor
        worker._poll_provider("good-prov", good_provider)

        # Good provider's observation was still recorded
        profiler.record_observation.assert_called_once_with(obs)


# ---------------------------------------------------------------------------
# Test: Monitor init failure
# ---------------------------------------------------------------------------


class TestMonitorInitFailure:
    """Tests for graceful degradation when monitor import fails."""

    def test_docker_import_error_disables_docker_monitor(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler()
        worker = ConnectionLogWorker(
            providers={},
            profiler=profiler,
            interval_s=60,
            docker_enabled=True,
            k8s_enabled=False,
        )

        # Simulate ImportError for Docker monitor
        with patch("enterprise.behavioral.connection_log_worker.ConnectionLogWorker._init_monitors") as mock_init:
            # We test _init_monitors directly
            pass

        # Instead, test the actual _init_monitors with a patched import
        with patch.dict("sys.modules", {"enterprise.behavioral.docker_network_monitor": None}):
            with patch(
                "builtins.__import__", side_effect=_import_error_for("enterprise.behavioral.docker_network_monitor")
            ):
                worker._init_monitors()

        assert worker._docker_monitor is None

    def test_k8s_import_error_disables_k8s_monitor(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler()
        worker = ConnectionLogWorker(
            providers={},
            profiler=profiler,
            interval_s=60,
            docker_enabled=False,
            k8s_enabled=True,
        )

        with patch.dict("sys.modules", {"enterprise.behavioral.k8s_network_monitor": None}):
            with patch(
                "builtins.__import__", side_effect=_import_error_for("enterprise.behavioral.k8s_network_monitor")
            ):
                worker._init_monitors()

        assert worker._k8s_monitor is None


def _import_error_for(target_module: str):
    """Create an import side_effect that raises ImportError only for the target module."""
    _real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _side_effect(name, *args, **kwargs):
        if name == target_module:
            raise ImportError(f"No module named '{target_module}'")
        return _real_import(name, *args, **kwargs)

    return _side_effect


# ---------------------------------------------------------------------------
# Test: Empty providers
# ---------------------------------------------------------------------------


class TestEmptyProviders:
    """Tests for no-op cycle with empty providers."""

    def test_empty_provider_list_is_noop(self):
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker

        profiler = _make_profiler()
        worker = ConnectionLogWorker(
            providers={},
            profiler=profiler,
            interval_s=60,
        )
        docker_monitor = _make_docker_monitor()
        worker._docker_monitor = docker_monitor

        # Run one cycle manually -- simulate what _loop does
        providers_snapshot = list(worker.providers.items())
        for provider_id, provider in providers_snapshot:
            worker._poll_provider(provider_id, provider)

        docker_monitor.poll_connections.assert_not_called()
        profiler.record_observation.assert_not_called()
