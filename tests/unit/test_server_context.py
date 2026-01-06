"""Tests for server context module."""

import pytest

from mcp_hangar.server.context import (
    ApplicationContext,
    get_context,
    ICommandBus,
    IEventBus,
    init_context,
    IQueryBus,
    IRateLimiter,
    ISecurityHandler,
    reset_context,
)


class TestProtocolInterfaces:
    """Tests for Protocol interface definitions."""

    def test_icommand_bus_is_protocol(self):
        """ICommandBus should be a Protocol."""
        from typing import Protocol

        assert issubclass(ICommandBus, Protocol)

    def test_iquery_bus_is_protocol(self):
        """IQueryBus should be a Protocol."""
        from typing import Protocol

        assert issubclass(IQueryBus, Protocol)

    def test_ievent_bus_is_protocol(self):
        """IEventBus should be a Protocol."""
        from typing import Protocol

        assert issubclass(IEventBus, Protocol)

    def test_irate_limiter_is_protocol(self):
        """IRateLimiter should be a Protocol."""
        from typing import Protocol

        assert issubclass(IRateLimiter, Protocol)

    def test_isecurity_handler_is_protocol(self):
        """ISecurityHandler should be a Protocol."""
        from typing import Protocol

        assert issubclass(ISecurityHandler, Protocol)


class TestApplicationContext:
    """Tests for ApplicationContext."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_get_context_lazy_initialization(self):
        """get_context() should lazily initialize context."""
        ctx = get_context()
        assert ctx is not None
        assert isinstance(ctx, ApplicationContext)

    def test_get_context_returns_same_instance(self):
        """get_context() should return the same instance."""
        ctx1 = get_context()
        ctx2 = get_context()
        assert ctx1 is ctx2

    def test_init_context_creates_new_context(self):
        """init_context() should create a new context with given runtime."""
        from mcp_hangar.bootstrap.runtime import create_runtime

        runtime = create_runtime()
        ctx = init_context(runtime)

        assert ctx is not None
        assert ctx.runtime is runtime

    def test_init_context_replaces_existing_context(self):
        """init_context() should replace existing context."""
        from mcp_hangar.bootstrap.runtime import create_runtime

        ctx1 = get_context()
        runtime = create_runtime()
        ctx2 = init_context(runtime)

        assert ctx1 is not ctx2
        assert get_context() is ctx2

    def test_reset_context_clears_context(self):
        """reset_context() should clear the context."""
        _ = get_context()  # Initialize
        reset_context()

        # After reset, get_context creates new instance
        from mcp_hangar.server.context import _context

        assert _context is None

    def test_context_has_repository(self):
        """Context should provide repository access."""
        ctx = get_context()
        assert ctx.repository is not None

    def test_context_has_command_bus(self):
        """Context should provide command bus access."""
        ctx = get_context()
        assert ctx.command_bus is not None

    def test_context_has_query_bus(self):
        """Context should provide query bus access."""
        ctx = get_context()
        assert ctx.query_bus is not None

    def test_context_has_event_bus(self):
        """Context should provide event bus access."""
        ctx = get_context()
        assert ctx.event_bus is not None

    def test_context_has_rate_limiter(self):
        """Context should provide rate limiter access."""
        ctx = get_context()
        assert ctx.rate_limiter is not None

    def test_context_has_security_handler(self):
        """Context should provide security handler access."""
        ctx = get_context()
        assert ctx.security_handler is not None

    def test_get_provider_returns_none_for_unknown(self):
        """get_provider() should return None for unknown provider."""
        ctx = get_context()
        assert ctx.get_provider("unknown-provider") is None

    def test_provider_exists_returns_false_for_unknown(self):
        """provider_exists() should return False for unknown provider."""
        ctx = get_context()
        assert ctx.provider_exists("unknown-provider") is False

    def test_get_group_returns_none_for_unknown(self):
        """get_group() should return None for unknown group."""
        ctx = get_context()
        assert ctx.get_group("unknown-group") is None

    def test_group_exists_returns_false_for_unknown(self):
        """group_exists() should return False for unknown group."""
        ctx = get_context()
        assert ctx.group_exists("unknown-group") is False

    def test_groups_default_to_empty_dict(self):
        """Context groups should default to empty dict."""
        ctx = get_context()
        assert ctx.groups == {}

    def test_discovery_orchestrator_default_to_none(self):
        """Context discovery_orchestrator should default to None."""
        ctx = get_context()
        assert ctx.discovery_orchestrator is None

    def test_group_rebalance_saga_default_to_none(self):
        """Context group_rebalance_saga should default to None."""
        ctx = get_context()
        assert ctx.group_rebalance_saga is None
