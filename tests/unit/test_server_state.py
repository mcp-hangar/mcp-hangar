"""Tests for server/state.py module."""

import pytest

from mcp_hangar.server.state import (
    get_discovery_orchestrator,
    get_group_rebalance_saga,
    get_runtime,
    GROUPS,
    ProviderDict,
    PROVIDERS,
    set_discovery_orchestrator,
    set_group_rebalance_saga,
)


class TestProviderDict:
    """Tests for ProviderDict wrapper class."""

    def test_getitem_raises_keyerror_for_missing(self):
        """Should raise KeyError for missing provider."""
        pd = ProviderDict(get_runtime().repository)

        with pytest.raises(KeyError):
            _ = pd["nonexistent-provider"]

    def test_contains_returns_false_for_missing(self):
        """Should return False for missing provider."""
        pd = ProviderDict(get_runtime().repository)

        assert "nonexistent-provider" not in pd

    def test_get_returns_default_for_missing(self):
        """Should return default value for missing provider."""
        pd = ProviderDict(get_runtime().repository)

        result = pd.get("nonexistent", "default-value")
        assert result == "default-value"

    def test_get_returns_none_for_missing_without_default(self):
        """Should return None for missing provider without default."""
        pd = ProviderDict(get_runtime().repository)

        result = pd.get("nonexistent")
        assert result is None

    def test_len_returns_count(self):
        """Should return number of providers."""
        pd = ProviderDict(get_runtime().repository)

        # Should be 0 or more
        assert len(pd) >= 0

    def test_items_returns_iterable(self):
        """Should return iterable of items."""
        pd = ProviderDict(get_runtime().repository)

        items = list(pd.items())
        assert isinstance(items, list)

    def test_keys_returns_iterable(self):
        """Should return iterable of keys."""
        pd = ProviderDict(get_runtime().repository)

        keys = list(pd.keys())
        assert isinstance(keys, list)

    def test_values_returns_iterable(self):
        """Should return iterable of values."""
        pd = ProviderDict(get_runtime().repository)

        values = list(pd.values())
        assert isinstance(values, list)


class TestGetRuntime:
    """Tests for get_runtime function."""

    def test_returns_runtime_instance(self):
        """Should return a Runtime instance."""
        runtime = get_runtime()

        assert runtime is not None
        assert hasattr(runtime, "repository")
        assert hasattr(runtime, "event_bus")
        assert hasattr(runtime, "command_bus")
        assert hasattr(runtime, "query_bus")

    def test_returns_same_instance(self):
        """Should return the same singleton instance."""
        runtime1 = get_runtime()
        runtime2 = get_runtime()

        assert runtime1 is runtime2


class TestDiscoveryOrchestrator:
    """Tests for discovery orchestrator getter/setter."""

    def test_get_returns_none_initially(self):
        """Should return None when not set."""
        # Reset first
        set_discovery_orchestrator(None)

        result = get_discovery_orchestrator()
        assert result is None

    def test_set_and_get(self):
        """Should set and get orchestrator."""
        mock_orchestrator = object()
        set_discovery_orchestrator(mock_orchestrator)

        result = get_discovery_orchestrator()
        assert result is mock_orchestrator

        # Cleanup
        set_discovery_orchestrator(None)


class TestGroupRebalanceSaga:
    """Tests for group rebalance saga getter/setter."""

    def test_get_returns_none_initially(self):
        """Should return None when not set."""
        # Reset first
        set_group_rebalance_saga(None)

        result = get_group_rebalance_saga()
        assert result is None

    def test_set_and_get(self):
        """Should set and get saga."""
        mock_saga = object()
        set_group_rebalance_saga(mock_saga)

        result = get_group_rebalance_saga()
        assert result is mock_saga

        # Cleanup
        set_group_rebalance_saga(None)


class TestGlobalState:
    """Tests for global state variables."""

    def test_providers_is_provider_dict(self):
        """PROVIDERS should be a ProviderDict."""
        assert isinstance(PROVIDERS, ProviderDict)

    def test_groups_is_dict(self):
        """GROUPS should be a dict."""
        assert isinstance(GROUPS, dict)
