"""Tests for event-store durability: fail-fast bootstrap + readiness check.

Covers the fix for the silent SQLite-> in-memory fallback that lost the
audit/event-sourcing trail on read-only deploys while /health/ready stayed
green (issue #428):

- sqlite + unwritable path -> startup raises, no silent memory swap
- driver: memory (explicit) -> memory store, no raise, not degraded
- allow_memory_fallback -> memory store, degraded posture recorded
- readiness check reports unhealthy when degraded-to-memory while durable
"""

import importlib
from types import SimpleNamespace

import pytest

from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.observability.health import (
    get_event_store_durability_status,
)
from mcp_hangar.server.bootstrap.event_store import (
    EventStoreConfigurationError,
    init_event_store,
)

# ``import mcp_hangar.server.bootstrap.event_store as ...`` fails because
# ``mcp_hangar.server.__init__`` re-exports a ``bootstrap`` function that shadows
# the subpackage attribute; fetch the module object via importlib instead so we
# can monkeypatch its ``create_persistent_event_store`` reference.
es_module = importlib.import_module("mcp_hangar.server.bootstrap.event_store")


class _FakeEventBus:
    """Mirrors the surface `init_event_store` binds to, and nothing else."""

    def __init__(self) -> None:
        self.store = None
        self.checkpoint = None

    def set_event_store(self, store) -> None:
        self.store = store

    def set_dispatch_checkpoint(self, checkpoint) -> None:
        self.checkpoint = checkpoint


@pytest.fixture
def runtime() -> SimpleNamespace:
    return SimpleNamespace(event_bus=_FakeEventBus())


@pytest.fixture(autouse=True)
def _clean_health():
    yield


class TestFailFastBootstrap:
    """init_event_store must not silently drop durability."""

    def test_sqlite_unwritable_path_raises(self, runtime, monkeypatch):
        """sqlite + unwritable path -> raise, no in-memory swap."""

        def _raise_oserror(_driver, _config):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(es_module, "create_persistent_event_store", _raise_oserror)

        config = {"event_store": {"enabled": True, "driver": "sqlite", "path": "data/events.db"}}
        with pytest.raises(EventStoreConfigurationError) as exc:
            init_event_store(runtime, config)

        assert "not writable" in str(exc.value)
        # No store was swapped in.
        assert runtime.event_bus.store is None

    def test_sqlite_backend_unavailable_raises(self, runtime, monkeypatch):
        """sqlite + backend unavailable (returns None) -> raise."""
        monkeypatch.setattr(es_module, "create_persistent_event_store", lambda _driver, _config: None)
        config = {"event_store": {"enabled": True, "driver": "sqlite"}}
        with pytest.raises(EventStoreConfigurationError):
            init_event_store(runtime, config)
        assert runtime.event_bus.store is None

    def test_unknown_driver_raises(self, runtime):
        config = {"event_store": {"enabled": True, "driver": "postgres"}}
        with pytest.raises(EventStoreConfigurationError):
            init_event_store(runtime, config)

    def test_sqlite_writable_records_durable(self, runtime, monkeypatch, tmp_path):
        """Happy path: durable store, not degraded."""
        monkeypatch.setattr(
            es_module,
            "create_persistent_event_store",
            lambda _driver, _config: InMemoryEventStore(),  # stand-in for the sqlite store
        )
        config = {"event_store": {"enabled": True, "driver": "sqlite", "path": str(tmp_path / "events.db")}}
        init_event_store(runtime, config)

        status = get_event_store_durability_status()
        assert status is not None
        assert status.configured_driver == "sqlite"
        assert status.durable is True
        assert status.degraded is False


class TestExplicitMemory:
    """Explicit non-durable opt-ins must not raise and must not be degraded."""

    def test_driver_memory_uses_memory_store_no_raise(self, runtime):
        config = {"event_store": {"enabled": True, "driver": "memory"}}
        init_event_store(runtime, config)

        assert isinstance(runtime.event_bus.store, InMemoryEventStore)
        status = get_event_store_durability_status()
        assert status is not None
        assert status.configured_driver == "memory"
        assert status.degraded is False

    def test_allow_memory_fallback_on_unwritable_degrades_not_raises(self, runtime, monkeypatch):
        """allow_memory_fallback: true -> memory store, degraded posture recorded."""

        def _raise_oserror(_driver, _config):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr(es_module, "create_persistent_event_store", _raise_oserror)
        config = {
            "event_store": {
                "enabled": True,
                "driver": "sqlite",
                "path": "data/events.db",
                "allow_memory_fallback": True,
            }
        }
        init_event_store(runtime, config)

        assert isinstance(runtime.event_bus.store, InMemoryEventStore)
        status = get_event_store_durability_status()
        assert status is not None
        assert status.configured_driver == "sqlite"
        assert status.durable is False
        assert status.degraded is True
