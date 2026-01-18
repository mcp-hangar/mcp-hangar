"""Tests for Event Sourced Provider."""

from mcp_hangar.domain.events import (
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from mcp_hangar.domain.model.event_sourced_provider import EventSourcedProvider, ProviderSnapshot
from mcp_hangar.domain.model.provider import ProviderState


class TestProviderSnapshot:
    """Test ProviderSnapshot dataclass."""

    def test_snapshot_creation(self):
        """Test creating a snapshot."""
        snapshot = ProviderSnapshot(
            provider_id="test-provider",
            mode="subprocess",
            state="ready",
            version=10,
            command=["python", "server.py"],
            image=None,
            endpoint=None,
            env={"VAR": "value"},
            idle_ttl_s=300,
            health_check_interval_s=60,
            max_consecutive_failures=3,
            consecutive_failures=0,
            total_failures=2,
            total_invocations=100,
            last_success_at=1000.0,
            last_failure_at=500.0,
            tool_names=["add", "subtract"],
            last_used=1000.0,
            meta={"started_at": 100.0},
        )

        assert snapshot.provider_id == "test-provider"
        assert snapshot.state == "ready"
        assert snapshot.version == 10

    def test_snapshot_to_dict(self):
        """Test snapshot to dictionary conversion."""
        snapshot = ProviderSnapshot(
            provider_id="p1",
            mode="subprocess",
            state="cold",
            version=5,
            command=None,
            image=None,
            endpoint=None,
            env={},
            idle_ttl_s=300,
            health_check_interval_s=60,
            max_consecutive_failures=3,
            consecutive_failures=0,
            total_failures=0,
            total_invocations=0,
            last_success_at=None,
            last_failure_at=None,
            tool_names=[],
            last_used=0.0,
            meta={},
        )

        d = snapshot.to_dict()

        assert d["provider_id"] == "p1"
        assert d["state"] == "cold"
        assert d["version"] == 5

    def test_snapshot_from_dict(self):
        """Test snapshot from dictionary."""
        d = {
            "provider_id": "p1",
            "mode": "docker",
            "state": "ready",
            "version": 15,
            "command": None,
            "image": "my-image:latest",
            "endpoint": None,
            "env": {"KEY": "val"},
            "idle_ttl_s": 600,
            "health_check_interval_s": 30,
            "max_consecutive_failures": 5,
            "consecutive_failures": 1,
            "total_failures": 3,
            "total_invocations": 50,
            "last_success_at": 2000.0,
            "last_failure_at": 1500.0,
            "tool_names": ["tool1"],
            "last_used": 2000.0,
            "meta": {"key": "value"},
        }

        snapshot = ProviderSnapshot.from_dict(d)

        assert snapshot.provider_id == "p1"
        assert snapshot.mode == "docker"
        assert snapshot.version == 15


class TestEventSourcedProvider:
    """Test EventSourcedProvider."""

    def test_create_empty_provider(self):
        """Test creating an empty provider."""
        provider = EventSourcedProvider(provider_id="test", mode="subprocess", command=["python", "server.py"])

        assert provider.provider_id == "test"
        assert provider.mode == "subprocess"
        assert provider.state == ProviderState.COLD
        assert provider.events_applied == 0

    def test_from_events_provider_started(self):
        """Test rebuilding from ProviderStarted event."""
        events = [ProviderStarted("p1", "subprocess", 5, 100.0)]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.state == ProviderState.READY
        assert provider.events_applied == 1

    def test_from_events_provider_stopped(self):
        """Test rebuilding with ProviderStopped event."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ProviderStopped("p1", "idle"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.state == ProviderState.COLD
        assert provider.events_applied == 2

    def test_from_events_provider_degraded(self):
        """Test rebuilding with ProviderDegraded event."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ProviderDegraded("p1", 3, 5, "timeout"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.state == ProviderState.DEGRADED
        assert provider.health.consecutive_failures == 3
        assert provider.health.total_failures == 5

    def test_from_events_state_changes(self):
        """Test rebuilding from ProviderStateChanged events."""
        events = [
            ProviderStateChanged("p1", "cold", "initializing"),
            ProviderStateChanged("p1", "initializing", "ready"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.state == ProviderState.READY

    def test_from_events_tool_completed(self):
        """Test health reset on tool completion."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ToolInvocationFailed("p1", "add", "c1", "error", "Error"),
            ToolInvocationCompleted("p1", "add", "c2", 50.0),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        # Tool completion should reset consecutive failures
        assert provider.health.consecutive_failures == 0

    def test_from_events_tool_failed(self):
        """Test health tracking on tool failure."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ToolInvocationFailed("p1", "add", "c1", "error1", "Error"),
            ToolInvocationFailed("p1", "add", "c2", "error2", "Error"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.health.consecutive_failures == 2
        assert provider.health.total_failures == 2

    def test_from_events_health_passed(self):
        """Test health reset on health check passed."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            HealthCheckFailed("p1", 1, "error"),
            HealthCheckPassed("p1", 50.0),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.health.consecutive_failures == 0

    def test_from_events_health_failed(self):
        """Test health tracking on health check failed."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            HealthCheckFailed("p1", 1, "error1"),
            HealthCheckFailed("p1", 2, "error2"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.health.consecutive_failures == 2

    def test_from_snapshot(self):
        """Test creating provider from snapshot."""
        snapshot = ProviderSnapshot(
            provider_id="p1",
            mode="subprocess",
            state="ready",
            version=10,
            command=["python", "server.py"],
            image=None,
            endpoint=None,
            env={},
            idle_ttl_s=300,
            health_check_interval_s=60,
            max_consecutive_failures=3,
            consecutive_failures=1,
            total_failures=5,
            total_invocations=100,
            last_success_at=1000.0,
            last_failure_at=500.0,
            tool_names=["add"],
            last_used=1000.0,
            meta={"started_at": 100.0},
        )

        provider = EventSourcedProvider.from_snapshot(snapshot)

        assert provider.provider_id == "p1"
        assert provider.state == ProviderState.READY
        assert provider.version == 10
        assert provider.health.consecutive_failures == 1
        assert provider.health.total_failures == 5

    def test_from_snapshot_with_events(self):
        """Test creating provider from snapshot plus subsequent events."""
        snapshot = ProviderSnapshot(
            provider_id="p1",
            mode="subprocess",
            state="ready",
            version=10,
            command=None,
            image=None,
            endpoint=None,
            env={},
            idle_ttl_s=300,
            health_check_interval_s=60,
            max_consecutive_failures=3,
            consecutive_failures=0,
            total_failures=0,
            total_invocations=0,
            last_success_at=None,
            last_failure_at=None,
            tool_names=[],
            last_used=0.0,
            meta={},
        )

        events = [
            ToolInvocationCompleted("p1", "add", "c1", 50.0),
            ProviderStopped("p1", "idle"),
        ]

        provider = EventSourcedProvider.from_snapshot(snapshot, events)

        assert provider.state == ProviderState.COLD
        assert provider.events_applied == 12  # 10 from snapshot + 2 events

    def test_create_snapshot(self):
        """Test creating snapshot from provider."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ToolInvocationCompleted("p1", "add", "c1", 50.0),
        ]

        provider = EventSourcedProvider.from_events(
            provider_id="p1",
            mode="subprocess",
            command=["python", "server.py"],
            events=events,
        )

        snapshot = provider.create_snapshot()

        assert snapshot.provider_id == "p1"
        assert snapshot.mode == "subprocess"
        assert snapshot.state == "ready"
        assert snapshot.command == ["python", "server.py"]

    def test_replay_to_version(self):
        """Test replaying to specific version (time travel)."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ToolInvocationCompleted("p1", "add", "c1", 50.0),
            ProviderStopped("p1", "idle"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        # Replay to version 1 (after first event)
        provider_v1 = provider.replay_to_version(1, events)

        assert provider_v1.state == ProviderState.READY
        assert provider_v1.events_applied == 1

    def test_uncommitted_events(self):
        """Test getting uncommitted events."""
        provider = EventSourcedProvider(provider_id="p1", mode="subprocess")

        # No uncommitted events initially
        assert provider.get_uncommitted_events() == []

    def test_mark_events_committed(self):
        """Test marking events as committed."""
        provider = EventSourcedProvider(provider_id="p1", mode="subprocess")

        # Simulate recording an event
        provider._record_event(ProviderStateChanged("p1", "cold", "initializing"))

        assert len(provider.get_uncommitted_events()) == 1

        provider.mark_events_committed()

        assert len(provider.get_uncommitted_events()) == 0

    def test_version_increments_on_events(self):
        """Test that version increments when events are applied."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ToolInvocationCompleted("p1", "add", "c1", 50.0),
            ProviderStopped("p1", "idle"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.version == 3

    def test_complex_event_sequence(self):
        """Test complex sequence of events."""
        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ToolInvocationCompleted("p1", "add", "c1", 50.0),
            ToolInvocationFailed("p1", "sub", "c2", "error", "Error"),
            ToolInvocationFailed("p1", "sub", "c3", "error", "Error"),
            HealthCheckPassed("p1", 30.0),
            ProviderDegraded("p1", 3, 3, "failures"),
            ProviderStopped("p1", "degraded"),
        ]

        provider = EventSourcedProvider.from_events(provider_id="p1", mode="subprocess", events=events)

        assert provider.state == ProviderState.COLD
        assert provider.events_applied == 7
