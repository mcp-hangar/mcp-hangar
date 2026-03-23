"""Unit tests for DiscoveryRegistry application service.

Tests cover all 7 methods: register_source, unregister_source, update_source,
get_source, get_all_sources, enable_source, disable_source, and orchestrator property.
"""

from unittest.mock import Mock

import pytest

from mcp_hangar.application.discovery.discovery_registry import DiscoveryRegistry
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode
from mcp_hangar.domain.value_objects.discovery import DiscoverySourceSpec


def _make_spec(source_id: str = "uuid-1", source_type: str = "docker") -> DiscoverySourceSpec:
    """Create a minimal DiscoverySourceSpec for testing."""
    return DiscoverySourceSpec(source_id=source_id, source_type=source_type, mode=DiscoveryMode.ADDITIVE)


def _make_registry() -> DiscoveryRegistry:
    """Create a DiscoveryRegistry backed by a mock orchestrator."""
    return DiscoveryRegistry(orchestrator=Mock())


class TestDiscoveryRegistryRegister:
    """Tests for register_source."""

    def test_register_source_stores_spec(self):
        """register_source stores spec; get_source returns it."""
        reg = _make_registry()
        spec = _make_spec()
        reg.register_source(spec)
        assert reg.get_source("uuid-1") == spec

    def test_register_source_overwrites_existing(self):
        """Registering same source_id again replaces the spec (idempotent)."""
        reg = _make_registry()
        spec1 = _make_spec()
        spec2 = DiscoverySourceSpec("uuid-1", "filesystem", DiscoveryMode.AUTHORITATIVE)
        reg.register_source(spec1)
        reg.register_source(spec2)
        assert reg.get_source("uuid-1").source_type == "filesystem"

    def test_register_source_appears_in_get_all(self):
        """Registered spec appears in get_all_sources()."""
        reg = _make_registry()
        reg.register_source(_make_spec("a"))
        reg.register_source(_make_spec("b"))
        ids = {s.source_id for s in reg.get_all_sources()}
        assert ids == {"a", "b"}


class TestDiscoveryRegistryUnregister:
    """Tests for unregister_source."""

    def test_unregister_removes_spec(self):
        """unregister_source removes spec; get_source returns None after."""
        reg = _make_registry()
        reg.register_source(_make_spec())
        reg.unregister_source("uuid-1")
        assert reg.get_source("uuid-1") is None

    def test_unregister_nonexistent_raises_key_error(self):
        """Unregistering unknown source_id raises KeyError."""
        reg = _make_registry()
        with pytest.raises(KeyError):
            reg.unregister_source("nonexistent")


class TestDiscoveryRegistryUpdate:
    """Tests for update_source."""

    def test_update_source_changes_enabled(self):
        """update_source with enabled=False produces spec with enabled=False."""
        reg = _make_registry()
        reg.register_source(_make_spec())
        updated = reg.update_source("uuid-1", enabled=False)
        assert updated.enabled is False
        assert reg.get_source("uuid-1").enabled is False

    def test_update_source_original_unchanged(self):
        """Original spec is not mutated (frozen dataclass, replace used)."""
        reg = _make_registry()
        spec = _make_spec()
        assert spec.enabled is True  # Original
        reg.register_source(spec)
        reg.update_source("uuid-1", enabled=False)
        # Original Python object unchanged
        assert spec.enabled is True

    def test_update_source_nonexistent_raises_key_error(self):
        """Updating unknown source_id raises KeyError."""
        reg = _make_registry()
        with pytest.raises(KeyError):
            reg.update_source("nonexistent", enabled=False)


class TestDiscoveryRegistryEnableDisable:
    """Tests for enable_source and disable_source."""

    def test_enable_source_sets_enabled_true(self):
        """enable_source returns spec with enabled=True."""
        reg = _make_registry()
        reg.register_source(DiscoverySourceSpec("uid", "docker", DiscoveryMode.ADDITIVE, enabled=False))
        result = reg.enable_source("uid")
        assert result.enabled is True

    def test_disable_source_sets_enabled_false(self):
        """disable_source returns spec with enabled=False."""
        reg = _make_registry()
        reg.register_source(_make_spec())
        result = reg.disable_source("uuid-1")
        assert result.enabled is False

    def test_enable_nonexistent_raises_key_error(self):
        """enable_source for unknown source_id raises KeyError."""
        reg = _make_registry()
        with pytest.raises(KeyError):
            reg.enable_source("missing")


class TestDiscoveryRegistryGetAll:
    """Tests for get_all_sources."""

    def test_get_all_sources_returns_snapshot(self):
        """get_all_sources returns a list snapshot; mutating list doesn't affect registry."""
        reg = _make_registry()
        reg.register_source(_make_spec("a"))
        snapshot = reg.get_all_sources()
        snapshot.clear()
        # Registry still has the source
        assert reg.get_source("a") is not None

    def test_get_all_sources_empty_initially(self):
        """New registry returns empty list."""
        reg = _make_registry()
        assert reg.get_all_sources() == []


class TestDiscoveryRegistryOrchestrator:
    """Tests for orchestrator property."""

    def test_orchestrator_property_returns_injected_orchestrator(self):
        """orchestrator property returns the same object injected in constructor."""
        orch = Mock()
        reg = DiscoveryRegistry(orchestrator=orch)
        assert reg.orchestrator is orch
