"""Tests for RichToolInvocationError and factory functions."""

import pytest

from mcp_hangar.errors import (
    create_argument_tool_error,
    create_crash_tool_error,
    create_provider_error,
    create_timeout_tool_error,
    ErrorCategory,
    RichToolInvocationError,
)


class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_user_error_value(self):
        assert ErrorCategory.USER_ERROR.value == "user_error"

    def test_provider_error_value(self):
        assert ErrorCategory.PROVIDER_ERROR.value == "provider_error"

    def test_infra_error_value(self):
        assert ErrorCategory.INFRA_ERROR.value == "infra_error"

    def test_is_string_enum(self):
        # ErrorCategory inherits from str, so .value can be used as strings
        assert ErrorCategory.USER_ERROR.value == "user_error"


class TestRichToolInvocationError:
    """Tests for RichToolInvocationError."""

    def test_basic_creation(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test-provider",
            tool_name="test-tool",
        )
        assert error.message == "Test error"
        assert error.provider == "test-provider"
        assert error.tool_name == "test-tool"

    def test_default_category_is_infra(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
        )
        assert error.category == ErrorCategory.INFRA_ERROR

    def test_str_includes_provider_and_tool(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="sqlite",
            tool_name="query",
        )
        output = str(error)
        assert "sqlite" in output
        assert "query" in output

    def test_str_includes_possible_causes(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
            possible_causes=["Cause 1", "Cause 2"],
        )
        output = str(error)
        assert "Possible causes:" in output
        assert "Cause 1" in output
        assert "Cause 2" in output

    def test_str_includes_recovery_hints(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
            recovery_hints=["Try this", "Or that"],
        )
        output = str(error)
        assert "What you can try:" in output
        assert "Try this" in output

    def test_str_includes_stderr_preview(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
            stderr_preview="Error: division by zero\nTraceback...",
        )
        output = str(error)
        assert "Provider stderr:" in output
        assert "division by zero" in output

    def test_str_includes_correlation_id(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
            correlation_id="abc-123",
        )
        output = str(error)
        assert "Correlation ID: abc-123" in output

    def test_str_includes_timeout_details(self):
        error = RichToolInvocationError(
            message="Timeout",
            provider="test",
            timeout_s=30.0,
            elapsed_s=30.5,
        )
        output = str(error)
        assert "Timeout: 30.0s" in output
        assert "elapsed: 30.50s" in output

    def test_str_includes_exit_code(self):
        error = RichToolInvocationError(
            message="Crash",
            provider="test",
            exit_code=137,
            signal_name="SIGKILL",
        )
        output = str(error)
        assert "Exit code: 137" in output
        assert "SIGKILL" in output

    def test_auto_generates_recovery_hints_for_user_error(self):
        error = RichToolInvocationError(
            message="Invalid args",
            provider="test",
            category=ErrorCategory.USER_ERROR,
        )
        assert len(error.recovery_hints) > 0
        assert any("argument" in h.lower() for h in error.recovery_hints)

    def test_auto_generates_recovery_hints_for_provider_error(self):
        error = RichToolInvocationError(
            message="Crash",
            provider="test",
            category=ErrorCategory.PROVIDER_ERROR,
        )
        assert len(error.recovery_hints) > 0
        assert any("restart" in h.lower() for h in error.recovery_hints)

    def test_auto_generates_recovery_hints_for_infra_error_with_timeout(self):
        error = RichToolInvocationError(
            message="Timeout",
            provider="test",
            category=ErrorCategory.INFRA_ERROR,
            timeout_s=30.0,
        )
        assert any("timeout" in h.lower() for h in error.recovery_hints)

    def test_custom_recovery_hints_are_preserved(self):
        custom_hints = ["Custom hint 1", "Custom hint 2"]
        error = RichToolInvocationError(
            message="Test",
            provider="test",
            recovery_hints=custom_hints,
        )
        assert error.recovery_hints == custom_hints

    def test_is_exception(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
        )
        assert isinstance(error, Exception)

    def test_can_be_raised(self):
        error = RichToolInvocationError(
            message="Test error",
            provider="test",
        )
        with pytest.raises(RichToolInvocationError) as exc_info:
            raise error
        assert exc_info.value.message == "Test error"


class TestCreateTimeoutToolError:
    """Tests for create_timeout_tool_error."""

    def test_creates_infra_error(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
        )
        assert error.category == ErrorCategory.INFRA_ERROR
        assert error.is_retryable is True

    def test_includes_timeout_in_message(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
        )
        assert "did not respond" in error.message.lower()

    def test_sets_timeout_values(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
        )
        assert error.timeout_s == 30.0
        assert error.elapsed_s == 30.5

    def test_suggests_longer_timeout_in_hints(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
        )
        hints_text = " ".join(error.recovery_hints)
        assert "timeout" in hints_text.lower()

    def test_includes_correlation_id(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
            correlation_id="test-123",
        )
        assert error.correlation_id == "test-123"

    def test_includes_arguments(self):
        args = {"sql": "SELECT * FROM users"}
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
            arguments=args,
        )
        assert error.arguments == args

    def test_has_possible_causes(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
        )
        assert len(error.possible_causes) > 0


class TestCreateCrashToolError:
    """Tests for create_crash_tool_error."""

    def test_creates_provider_error(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=1,
        )
        assert error.category == ErrorCategory.PROVIDER_ERROR

    def test_is_retryable_because_provider_restarts(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=1,
        )
        assert error.is_retryable is True

    def test_detects_sigkill_from_exit_code_137(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=137,
        )
        assert error.signal_name == "SIGKILL"
        assert any("memory" in cause.lower() for cause in error.possible_causes)

    def test_detects_sigkill_from_negative_exit_code(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=-9,  # -SIGKILL
        )
        assert error.signal_name == "SIGKILL"

    def test_detects_sigsegv_from_exit_code_139(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=139,
        )
        assert error.signal_name == "SIGSEGV"
        assert any("segmentation" in cause.lower() for cause in error.possible_causes)

    def test_includes_stderr_preview(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=1,
            stderr_preview="Error: something went wrong",
        )
        assert error.stderr_preview == "Error: something went wrong"

    def test_includes_elapsed_time(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=1,
            elapsed_s=45.2,
        )
        assert error.elapsed_s == 45.2

    def test_message_mentions_crash(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=1,
        )
        assert "crashed" in error.message.lower()

    def test_handles_none_exit_code(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=None,
        )
        assert error.exit_code is None
        assert error.signal_name is None


class TestCreateArgumentToolError:
    """Tests for create_argument_tool_error."""

    def test_creates_user_error(self):
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args={"query": "SELECT *"},
        )
        assert error.category == ErrorCategory.USER_ERROR

    def test_is_not_retryable(self):
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args={"query": "SELECT *"},
        )
        assert error.is_retryable is False

    def test_includes_provided_args(self):
        args = {"query": "SELECT *"}
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args=args,
        )
        assert error.arguments == args

    def test_includes_expected_schema(self):
        schema = {"properties": {"sql": {"type": "string"}}}
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args={"query": "SELECT *"},
            expected_schema=schema,
        )
        assert error.expected_schema == schema

    def test_includes_hint_in_recovery(self):
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args={"query": "SELECT *"},
            hint="Did you mean 'sql' instead of 'query'?",
        )
        assert "sql" in " ".join(error.recovery_hints)

    def test_message_mentions_invalid_arguments(self):
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args={},
        )
        assert "invalid arguments" in error.message.lower()


class TestCreateProviderError:
    """Tests for create_provider_error."""

    def test_creates_provider_error(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="division by zero",
        )
        assert error.category == ErrorCategory.PROVIDER_ERROR

    def test_includes_error_message(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="division by zero",
        )
        assert "division by zero" in error.message

    def test_default_is_retryable(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="some error",
        )
        assert error.is_retryable is True

    def test_can_be_not_retryable(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="permanent error",
            is_retryable=False,
        )
        assert error.is_retryable is False

    def test_includes_stderr_preview(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="error",
            stderr_preview="Traceback...",
        )
        assert error.stderr_preview == "Traceback..."

    def test_includes_correlation_id(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="error",
            correlation_id="test-456",
        )
        assert error.correlation_id == "test-456"

    def test_has_possible_causes(self):
        error = create_provider_error(
            provider="math",
            tool="divide",
            error_message="error",
        )
        assert len(error.possible_causes) > 0


class TestRichToolInvocationErrorIntegration:
    """Integration tests for error formatting."""

    def test_timeout_error_full_output(self):
        error = create_timeout_tool_error(
            provider="sqlite",
            tool="query",
            timeout_s=30.0,
            elapsed_s=30.5,
            correlation_id="abc-123",
            arguments={"sql": "SELECT * FROM users"},
        )
        output = str(error)

        # Check all sections are present
        assert "RichToolInvocationError" in output
        assert "sqlite" in output
        assert "query" in output
        assert "Possible causes:" in output
        assert "Technical details:" in output
        assert "What you can try:" in output
        assert "abc-123" in output

    def test_crash_error_full_output(self):
        error = create_crash_tool_error(
            provider="math",
            tool="calculate",
            exit_code=137,
            stderr_preview="Killed\n",
            correlation_id="def-456",
        )
        output = str(error)

        assert "crashed" in output.lower()
        assert "137" in output
        assert "SIGKILL" in output
        assert "Provider stderr:" in output
        assert "def-456" in output

    def test_argument_error_full_output(self):
        error = create_argument_tool_error(
            provider="sqlite",
            tool="query",
            provided_args={"query": "SELECT *"},
            hint="Did you mean 'sql' instead of 'query'?",
            correlation_id="ghi-789",
        )
        output = str(error)

        assert "Invalid arguments" in output
        assert "sqlite" in output
        assert "What you can try:" in output
        assert "sql" in output
        assert "ghi-789" in output
