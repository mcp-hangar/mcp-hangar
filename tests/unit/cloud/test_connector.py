"""Tests for cloud.connector -- event filtering and provider snapshots."""

from dataclasses import dataclass
from unittest.mock import MagicMock

from mcp_hangar.cloud.config import CloudConfig
from mcp_hangar.cloud.connector import CloudConnector, _FORWARDED_EVENT_TYPES


@dataclass
class FakeEvent:
    """Minimal DomainEvent stand-in for testing on_event filtering."""

    def __init__(self, name: str):
        self._name = name

    @property
    def __class__(self):
        # Hack so type(event).__name__ returns the desired string
        return type(self._name, (), {"__name__": self._name})

    def to_dict(self):
        return {"event_type": self._name, "data": "test"}


class _FakeProvider:
    def __init__(self, state="ready", mode="subprocess", tools=None):
        self.state = state
        self.mode = mode
        self.tools = tools or []


class TestConnectorEventFilter:
    def _make_connector(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        return CloudConnector(config=cfg, providers={})

    def test_forwards_known_event_types(self):
        conn = self._make_connector()
        for etype in ["ToolInvocationCompleted", "ProviderStarted", "HealthCheckFailed"]:
            event = MagicMock()
            type(event).__name__ = etype
            event.to_dict.return_value = {"event_type": etype}
            conn.on_event(event)
        assert conn._buffer.size == 3

    def test_ignores_unknown_event_types(self):
        conn = self._make_connector()
        event = MagicMock()
        type(event).__name__ = "InternalDebugEvent"
        conn.on_event(event)
        assert conn._buffer.size == 0

    def test_all_forwarded_types_are_strings(self):
        for t in _FORWARDED_EVENT_TYPES:
            assert isinstance(t, str)
            assert t[0].isupper()  # PascalCase


class TestConnectorProviderSnapshots:
    def test_build_snapshots(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        providers = {
            "github": _FakeProvider(state="ready", mode="subprocess", tools=[]),
            "slack": _FakeProvider(state="dead", mode="docker", tools=[]),
        }
        conn = CloudConnector(config=cfg, providers=providers)
        snaps = conn._build_provider_snapshots()
        assert len(snaps) == 2
        github = next(s for s in snaps if s["id"] == "github")
        assert github["status"] == "READY"
        assert github["health"] == "healthy"
        slack = next(s for s in snaps if s["id"] == "slack")
        assert slack["status"] == "DEAD"
        assert slack["health"] == "unhealthy"

    def test_provider_counts(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        providers = {
            "a": _FakeProvider(state="ready"),
            "b": _FakeProvider(state="ready"),
            "c": _FakeProvider(state="dead"),
        }
        conn = CloudConnector(config=cfg, providers=providers)
        total, healthy = conn._provider_counts()
        assert total == 3
        assert healthy == 2

    def test_empty_providers(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        conn = CloudConnector(config=cfg, providers={})
        assert conn._build_provider_snapshots() == []
        assert conn._provider_counts() == (0, 0)
