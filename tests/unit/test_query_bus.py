"""Tests for Query Bus infrastructure."""

from unittest.mock import Mock

import pytest

from mcp_hangar.infrastructure.query_bus import (
    get_query_bus,
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetSystemMetricsQuery,
    ListProvidersQuery,
    Query,
    QueryBus,
    QueryHandler,
)


class TestQueries:
    """Test Query classes."""

    def test_list_providers_query(self):
        """Test ListProvidersQuery creation."""
        query = ListProvidersQuery()

        assert query.state_filter is None

    def test_list_providers_query_with_filter(self):
        """Test ListProvidersQuery with state filter."""
        query = ListProvidersQuery(state_filter="ready")

        assert query.state_filter == "ready"

    def test_get_provider_query(self):
        """Test GetProviderQuery creation."""
        query = GetProviderQuery(provider_id="test-provider")

        assert query.provider_id == "test-provider"

    def test_get_provider_tools_query(self):
        """Test GetProviderToolsQuery creation."""
        query = GetProviderToolsQuery(provider_id="test-provider")

        assert query.provider_id == "test-provider"

    def test_get_provider_health_query(self):
        """Test GetProviderHealthQuery creation."""
        query = GetProviderHealthQuery(provider_id="test-provider")

        assert query.provider_id == "test-provider"

    def test_get_system_metrics_query(self):
        """Test GetSystemMetricsQuery creation."""
        query = GetSystemMetricsQuery()

        assert isinstance(query, Query)


class TestQueryBus:
    """Test QueryBus functionality."""

    def test_register_handler(self):
        """Test registering a query handler."""
        bus = QueryBus()
        handler = Mock(spec=QueryHandler)

        bus.register(ListProvidersQuery, handler)

        assert ListProvidersQuery in bus._handlers

    def test_register_multiple_handlers(self):
        """Test registering multiple handlers for different queries."""
        bus = QueryBus()
        handler1 = Mock(spec=QueryHandler)
        handler2 = Mock(spec=QueryHandler)

        bus.register(ListProvidersQuery, handler1)
        bus.register(GetProviderQuery, handler2)

        assert len(bus._handlers) == 2

    def test_execute_query_calls_handler(self):
        """Test executing a query calls the registered handler."""
        bus = QueryBus()
        handler = Mock(spec=QueryHandler)
        handler.handle.return_value = [{"id": "p1"}, {"id": "p2"}]

        bus.register(ListProvidersQuery, handler)

        query = ListProvidersQuery()
        result = bus.execute(query)

        handler.handle.assert_called_once_with(query)
        assert result == [{"id": "p1"}, {"id": "p2"}]

    def test_execute_query_without_handler_raises(self):
        """Test executing unregistered query raises ValueError."""
        bus = QueryBus()
        query = ListProvidersQuery()

        with pytest.raises(ValueError):
            bus.execute(query)

    def test_execute_returns_handler_result(self):
        """Test execute returns the handler's result."""
        bus = QueryBus()
        handler = Mock(spec=QueryHandler)
        handler.handle.return_value = {"provider_id": "test", "state": "ready"}

        bus.register(GetProviderQuery, handler)

        query = GetProviderQuery(provider_id="test")
        result = bus.execute(query)

        assert result == {"provider_id": "test", "state": "ready"}

    def test_handler_exception_propagates(self):
        """Test that handler exceptions propagate."""
        bus = QueryBus()
        handler = Mock(spec=QueryHandler)
        handler.handle.side_effect = ValueError("Provider not found")

        bus.register(GetProviderQuery, handler)

        query = GetProviderQuery(provider_id="nonexistent")

        with pytest.raises(ValueError, match="Provider not found"):
            bus.execute(query)

    def test_get_query_bus_returns_singleton(self):
        """Test get_query_bus returns same instance."""
        bus1 = get_query_bus()
        bus2 = get_query_bus()

        assert bus1 is bus2

    def test_query_bus_can_be_reset(self):
        """Test query bus can be cleared."""
        bus = QueryBus()
        handler = Mock(spec=QueryHandler)

        bus.register(ListProvidersQuery, handler)

        assert len(bus._handlers) == 1

        bus._handlers.clear()

        assert len(bus._handlers) == 0


class TestQueryHandlerInterface:
    """Test QueryHandler abstract interface."""

    def test_handler_interface_requires_handle(self):
        """Test that QueryHandler requires handle method."""

        class ConcreteHandler(QueryHandler):
            def handle(self, query):
                return {"result": "data"}

        handler = ConcreteHandler()
        result = handler.handle(Mock())

        assert result == {"result": "data"}

    def test_handler_without_handle_raises(self):
        """Test that incomplete handler raises TypeError."""
        with pytest.raises(TypeError):

            class IncompleteHandler(QueryHandler):
                pass

            IncompleteHandler()


class TestQueryIntegration:
    """Integration tests for query handling."""

    def test_full_query_flow(self):
        """Test complete query registration and execution flow."""
        bus = QueryBus()

        class TestHandler(QueryHandler):
            def handle(self, query):
                return [
                    {"id": "p1", "state": "ready"},
                    {"id": "p2", "state": "cold"},
                ]

        bus.register(ListProvidersQuery, TestHandler())

        query = ListProvidersQuery()
        result = bus.execute(query)

        assert len(result) == 2
        assert result[0]["id"] == "p1"

    def test_different_queries_different_handlers(self):
        """Test different queries go to different handlers."""
        bus = QueryBus()

        class ListHandler(QueryHandler):
            def handle(self, query):
                return [{"id": "p1"}, {"id": "p2"}]

        class GetHandler(QueryHandler):
            def handle(self, query):
                return {"id": query.provider_id, "details": True}

        bus.register(ListProvidersQuery, ListHandler())
        bus.register(GetProviderQuery, GetHandler())

        list_result = bus.execute(ListProvidersQuery())
        get_result = bus.execute(GetProviderQuery(provider_id="test"))

        assert len(list_result) == 2
        assert get_result["id"] == "test"
        assert get_result["details"] is True

    def test_query_with_filter(self):
        """Test query with filter parameter."""
        bus = QueryBus()

        class FilterHandler(QueryHandler):
            def handle(self, query):
                all_providers = [
                    {"id": "p1", "state": "ready"},
                    {"id": "p2", "state": "cold"},
                    {"id": "p3", "state": "ready"},
                ]
                if query.state_filter:
                    return [p for p in all_providers if p["state"] == query.state_filter]
                return all_providers

        bus.register(ListProvidersQuery, FilterHandler())

        # No filter
        all_result = bus.execute(ListProvidersQuery())
        assert len(all_result) == 3

        # With filter
        ready_result = bus.execute(ListProvidersQuery(state_filter="ready"))
        assert len(ready_result) == 2
        assert all(p["state"] == "ready" for p in ready_result)

    def test_query_returns_immutable_data(self):
        """Test that queries return data that can be safely consumed."""
        bus = QueryBus()

        class DataHandler(QueryHandler):
            def __init__(self):
                self._data = {"value": 42}

            def handle(self, query):
                return self._data.copy()

        handler = DataHandler()
        bus.register(GetProviderQuery, handler)

        query = GetProviderQuery(provider_id="test")
        result = bus.execute(query)

        # Modifying result shouldn't affect internal state
        result["value"] = 100

        result2 = bus.execute(query)
        assert result2["value"] == 42
