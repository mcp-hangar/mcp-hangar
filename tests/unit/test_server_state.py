"""Tests for server/state.py module."""

import warnings
from unittest.mock import MagicMock

import pytest

from mcp_hangar.server import state
from mcp_hangar.server.state import (
    get_discovery_orchestrator,
    get_group_rebalance_saga,
    get_runtime,
    GROUPS,
    set_discovery_orchestrator,
    set_group_rebalance_saga,
)


class TestDeprecatedRuntimeExports:
    """Tests for deprecated lazy runtime exports."""

    def test_providers_resolves_repository_with_deprecation_warning(self):
        """PROVIDERS should lazily resolve to the runtime repository."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            providers = state.PROVIDERS

        assert providers is get_runtime().repository
        assert any(item.category is DeprecationWarning for item in caught)

    def test_provider_repository_resolves_lazily(self):
        """PROVIDER_REPOSITORY should lazily resolve to the repository."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            repository = state.PROVIDER_REPOSITORY

        assert repository is get_runtime().repository
        assert any(item.category is DeprecationWarning for item in caught)


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
        mock_orchestrator = MagicMock()
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
        mock_saga = MagicMock()
        set_group_rebalance_saga(mock_saga)

        result = get_group_rebalance_saga()
        assert result is mock_saga

        # Cleanup
        set_group_rebalance_saga(None)


class TestGlobalState:
    """Tests for global state variables."""

    def test_groups_is_dict(self):
        """GROUPS should be a dict."""
        assert isinstance(GROUPS, dict)
