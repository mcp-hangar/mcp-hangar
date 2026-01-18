"""Tests for server tools - provider module helper functions."""

import pytest

from mcp_hangar.server.context import get_context, reset_context
from mcp_hangar.server.tools.provider import (
    DEFAULT_GROUP_RETRY_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    _extract_error_text,
    _get_tools_for_group,
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

    def test_retry_attempts_is_at_least_one(self):
        """Should have at least one retry attempt."""
        assert DEFAULT_GROUP_RETRY_ATTEMPTS >= 1


class TestExtractErrorText:
    """Tests for _extract_error_text function."""

    def test_extracts_from_string(self):
        """Should return string as-is."""
        result = _extract_error_text("Error message")
        assert result == "Error message"

    def test_extracts_from_list_of_dicts(self):
        """Should extract text from list of dicts with type/text."""
        content = [
            {"type": "text", "text": "First error"},
            {"type": "text", "text": "Second error"},
        ]
        result = _extract_error_text(content)
        assert "First error" in result
        assert "Second error" in result

    def test_extracts_from_list_of_strings(self):
        """Should extract text from list of strings."""
        content = ["Error 1", "Error 2"]
        result = _extract_error_text(content)
        assert "Error 1" in result
        assert "Error 2" in result

    def test_extracts_from_dict_with_text(self):
        """Should extract text field from dict."""
        content = {"text": "Error from dict"}
        result = _extract_error_text(content)
        assert result == "Error from dict"

    def test_extracts_from_dict_with_message(self):
        """Should extract message field from dict when text missing."""
        content = {"message": "Error message"}
        result = _extract_error_text(content)
        assert result == "Error message"

    def test_handles_empty_list(self):
        """Should return 'Unknown error' for empty list."""
        result = _extract_error_text([])
        assert result == "Unknown error"

    def test_handles_none(self):
        """Should return 'Unknown error' for None."""
        result = _extract_error_text(None)
        assert result == "Unknown error"

    def test_handles_mixed_list(self):
        """Should handle list with mixed types."""
        content = [
            {"type": "text", "text": "Dict error"},
            "String error",
            {"no_text_key": "ignored"},
        ]
        result = _extract_error_text(content)
        assert "Dict error" in result
        assert "String error" in result

    def test_converts_other_types_to_string(self):
        """Should convert other types to string."""
        result = _extract_error_text(12345)
        assert result == "12345"

    def test_handles_empty_text_in_dict(self):
        """Should handle dict with empty text field."""
        content = [{"type": "text", "text": ""}]
        result = _extract_error_text(content)
        assert result == "Unknown error"


class TestGetToolsForProvider:
    """Tests for _get_tools_for_provider function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_function_exists(self):
        """Function should exist and be callable."""
        assert callable(_get_tools_for_provider)

    def test_raises_for_unknown_provider(self):
        """Should raise error for unknown provider."""
        ctx = get_context()
        assert not ctx.provider_exists("unknown-provider")

        # Function doesn't validate - returns None provider, then AttributeError
        with pytest.raises((KeyError, AttributeError)):
            _get_tools_for_provider("unknown-provider")


class TestGetToolsForGroup:
    """Tests for _get_tools_for_group function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_function_exists(self):
        """Function should exist and be callable."""
        assert callable(_get_tools_for_group)

    def test_raises_for_unknown_group(self):
        """Should raise error for unknown group."""
        # ctx.get_group returns None for unknown, leading to AttributeError
        with pytest.raises((KeyError, AttributeError)):
            _get_tools_for_group("unknown-group")


class TestInvokeOnProvider:
    """Tests for _invoke_on_provider function."""

    @pytest.fixture(autouse=True)
    def reset_context_fixture(self):
        """Reset context before and after each test."""
        reset_context()
        yield
        reset_context()

    def test_function_exists(self):
        """Function should exist and be callable."""
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

    def test_has_progress_parameter(self):
        """Function should have optional progress parameter."""
        import inspect

        sig = inspect.signature(_invoke_on_provider)
        params = sig.parameters

        assert "progress" in params
        # Should have default value (optional)
        assert params["progress"].default is not inspect.Parameter.empty or params["progress"].default is None
