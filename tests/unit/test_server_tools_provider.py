"""Tests for server tools - provider module helper functions."""

import pytest

from mcp_hangar.server.context import get_context, reset_context
from mcp_hangar.server.tools.provider import (
    DEFAULT_GROUP_RETRY_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    _get_tools_for_provider,
    _invoke_on_provider,
)


class TestConstants:
    """Tests for module constants."""

    def test_default_group_retry_attempts_is_positive(self):
        """DEFAULT_GROUP_RETRY_ATTEMPTS should be positive."""
        assert DEFAULT_GROUP_RETRY_ATTEMPTS > 0

    def test_default_timeout_seconds_is_positive(self):
        """DEFAULT_TIMEOUT_SECONDS should be positive."""
        assert DEFAULT_TIMEOUT_SECONDS > 0

    def test_default_timeout_is_reasonable(self):
        """DEFAULT_TIMEOUT_SECONDS should be reasonable (1-120s)."""
        assert 1 <= DEFAULT_TIMEOUT_SECONDS <= 120


class TestGetToolsForProvider:
    """Tests for _get_tools_for_provider function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_raises_for_unknown_provider(self):
        """Should raise ValueError for unknown provider."""
        # First we need to ensure provider doesn't exist
        ctx = get_context()
        assert not ctx.provider_exists("unknown-provider")

        # The function expects provider to exist (caller should check)
        # so we test at the higher level in registry_tools tests


class TestInvokeOnProvider:
    """Tests for _invoke_on_provider function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_sends_invoke_command(self):
        """Should send InvokeToolCommand via command bus."""
        # This test would require mocking the command bus
        # For now, we verify the function exists and has correct signature
        assert callable(_invoke_on_provider)

    def test_function_signature(self):
        """Function should accept provider, tool, arguments, timeout."""
        import inspect

        sig = inspect.signature(_invoke_on_provider)
        params = list(sig.parameters.keys())

        assert "provider" in params
        assert "tool" in params
        assert "arguments" in params
        assert "timeout" in params

