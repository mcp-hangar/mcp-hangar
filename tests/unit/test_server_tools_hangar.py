"""Tests for server tools - hangar module."""

import pytest

from mcp_hangar.application.queries import register_all_handlers as register_query_handlers
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.domain.repository import InMemoryProviderRepository
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.context import init_context, reset_context
from mcp_hangar.server.tools.hangar import hangar_list


class TestHangarList:
    """Tests for hangar_list function."""

    @pytest.fixture(autouse=True)
    def setup_context(self):
        """Create fresh runtime and context before each test."""
        reset_context()

        # Create fresh components to avoid singleton issues
        fresh_repo = InMemoryProviderRepository()
        fresh_query_bus = QueryBus()

        # Create runtime with fresh components
        runtime = create_runtime(
            repository=fresh_repo,
            query_bus=fresh_query_bus,
        )

        # Initialize context
        init_context(runtime)

        # Register handlers on fresh query bus
        register_query_handlers(fresh_query_bus, fresh_repo)

        yield
        reset_context()

    def test_returns_dict_with_providers_and_groups(self):
        """hangar_list should return dict with providers and groups keys."""
        result = hangar_list()

        assert isinstance(result, dict)
        assert "providers" in result
        assert "groups" in result

    def test_returns_empty_providers_when_none_managed(self):
        """hangar_list should return empty providers list when none managed."""
        result = hangar_list()

        assert result["providers"] == []

    def test_returns_empty_groups_when_none_managed(self):
        """hangar_list should return empty groups list when none managed."""
        result = hangar_list()

        assert result["groups"] == []

    def test_state_filter_none_returns_all(self):
        """hangar_list with state_filter=None should return all providers."""
        result = hangar_list(state_filter=None)

        assert isinstance(result["providers"], list)
        assert isinstance(result["groups"], list)

    def test_state_filter_cold_filters_providers(self):
        """hangar_list with state_filter='cold' should filter providers."""
        result = hangar_list(state_filter="cold")

        # All returned providers should be in 'cold' state
        for provider in result["providers"]:
            assert provider.get("state") == "cold"

    def test_state_filter_ready_filters_providers(self):
        """hangar_list with state_filter='ready' should filter providers."""
        result = hangar_list(state_filter="ready")

        # With no providers, should return empty
        assert result["providers"] == []

    def test_state_filter_unknown_returns_empty(self):
        """hangar_list with unknown state_filter should return empty."""
        result = hangar_list(state_filter="nonexistent_state")

        assert result["providers"] == []
        assert result["groups"] == []
