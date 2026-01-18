"""Tests for server validation module."""

import pytest

from mcp_hangar.server.context import reset_context
from mcp_hangar.server.validation import (
    check_rate_limit,
    tool_error_hook,
    tool_error_mapper,
    validate_arguments_input,
    validate_provider_id_input,
    validate_timeout_input,
    validate_tool_name_input,
)


class TestToolErrorMapper:
    """Tests for tool_error_mapper function."""

    def test_maps_value_error(self):
        """Should map ValueError to ToolErrorPayload."""
        exc = ValueError("test error message")
        result = tool_error_mapper(exc)

        assert result.error == "test error message"
        assert result.error_type == "ValueError"
        assert result.details == {}

    def test_maps_runtime_error(self):
        """Should map RuntimeError to ToolErrorPayload."""
        exc = RuntimeError("runtime error")
        result = tool_error_mapper(exc)

        assert result.error == "runtime error"
        assert result.error_type == "RuntimeError"

    def test_maps_exception_with_empty_message(self):
        """Should handle exception with empty message."""
        exc = ValueError("")
        result = tool_error_mapper(exc)

        assert result.error == "unknown error"
        assert result.error_type == "ValueError"


class TestToolErrorHook:
    """Tests for tool_error_hook function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_hook_does_not_raise(self):
        """Hook should not raise exceptions."""
        exc = ValueError("test")
        context = {"provider_id": "test-provider"}

        # Should not raise
        tool_error_hook(exc, context)

    def test_hook_handles_missing_context(self):
        """Hook should handle missing context keys."""
        exc = ValueError("test")
        context = {}

        # Should not raise
        tool_error_hook(exc, context)


class TestValidateProviderIdInput:
    """Tests for validate_provider_id_input function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_valid_provider_id_does_not_raise(self):
        """Valid provider ID should not raise."""
        # Should not raise
        validate_provider_id_input("valid-provider-id")

    def test_valid_provider_id_with_underscores(self):
        """Provider ID with underscores should be valid."""
        validate_provider_id_input("my_provider_123")

    def test_invalid_provider_id_raises(self):
        """Invalid provider ID should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_provider_id_input("../../../etc/passwd")

        assert "invalid_provider_id" in str(exc_info.value)

    def test_empty_provider_id_raises(self):
        """Empty provider ID should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_provider_id_input("")

        assert "invalid_provider_id" in str(exc_info.value)


class TestValidateToolNameInput:
    """Tests for validate_tool_name_input function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_valid_tool_name_does_not_raise(self):
        """Valid tool name should not raise."""
        validate_tool_name_input("my_tool")

    def test_valid_tool_name_with_dashes(self):
        """Tool name with dashes should be valid."""
        validate_tool_name_input("my-tool-name")

    def test_invalid_tool_name_raises(self):
        """Invalid tool name should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_tool_name_input("../invalid")

        assert "invalid_tool_name" in str(exc_info.value)


class TestValidateArgumentsInput:
    """Tests for validate_arguments_input function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_valid_arguments_does_not_raise(self):
        """Valid arguments should not raise."""
        validate_arguments_input({"key": "value", "number": 42})

    def test_empty_arguments_does_not_raise(self):
        """Empty arguments should be valid."""
        validate_arguments_input({})

    def test_nested_arguments_valid(self):
        """Nested arguments should be valid."""
        validate_arguments_input({"nested": {"key": "value"}})


class TestValidateTimeoutInput:
    """Tests for validate_timeout_input function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_valid_timeout_does_not_raise(self):
        """Valid timeout should not raise."""
        validate_timeout_input(30.0)

    def test_small_timeout_valid(self):
        """Small positive timeout should be valid."""
        validate_timeout_input(0.1)

    def test_negative_timeout_raises(self):
        """Negative timeout should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_timeout_input(-1.0)

        assert "invalid_timeout" in str(exc_info.value)

    def test_zero_timeout_raises(self):
        """Zero timeout should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_timeout_input(0.0)

        assert "invalid_timeout" in str(exc_info.value)

    def test_very_large_timeout_raises(self):
        """Very large timeout should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_timeout_input(1000000.0)

        assert "invalid_timeout" in str(exc_info.value)


class TestCheckRateLimit:
    """Tests for check_rate_limit function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_rate_limit_allows_request(self):
        """Rate limit should allow normal requests."""
        # Should not raise with default config
        check_rate_limit("test-key")

    def test_rate_limit_with_global_key(self):
        """Rate limit should work with global key."""
        check_rate_limit("global")
