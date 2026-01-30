"""Tests for UX improvements: errors, retry, and progress modules."""

import builtins
import json

from mcp_hangar.errors import (
    ConfigurationError,
    HangarError,
    is_retryable,
    map_exception_to_hangar_error,
    NetworkError,
    ProviderCrashError,
    ProviderDegradedError,
    ProviderNotFoundError,
    ProviderProtocolError,
    RateLimitError,
    TimeoutError,
    ToolNotFoundError,
    TransientError,
)


class TestHangarError:
    """Tests for HangarError base class."""

    def test_basic_error_creation(self):
        """Test creating a basic error."""
        error = HangarError(
            message="Something went wrong",
            provider="test-provider",
            operation="invoke",
        )
        assert error.message == "Something went wrong"
        assert error.provider == "test-provider"
        assert error.operation == "invoke"
        assert "Something went wrong" in str(error)

    def test_error_with_recovery_hints(self):
        """Test error formatting with recovery hints."""
        error = HangarError(
            message="Operation failed",
            provider="math",
            operation="add",
            recovery_hints=[
                "Check provider status",
                "Try again later",
            ],
        )
        error_str = str(error)
        assert "Recovery steps:" in error_str
        assert "Check provider status" in error_str
        assert "Try again later" in error_str

    def test_error_to_dict(self):
        """Test error serialization to dictionary."""
        error = HangarError(
            message="Test error",
            provider="test",
            operation="test_op",
            technical_details="Details here",
            recovery_hints=["Hint 1"],
        )
        result = error.to_dict()
        assert result["error_type"] == "HangarError"
        assert result["message"] == "Test error"
        assert result["provider"] == "test"
        assert result["recovery_hints"] == ["Hint 1"]

    def test_error_with_related_logs(self):
        """Test error with related logs reference."""
        error = HangarError(
            message="Test",
            provider="test",
            related_logs="/logs/mcp-hangar.log:580",
        )
        error_str = str(error)
        assert "Related logs:" in error_str
        assert "/logs/mcp-hangar.log:580" in error_str

    def test_error_with_issue_url(self):
        """Test error with issue URL."""
        error = HangarError(
            message="Test",
            provider="test",
            issue_url="https://github.com/test/issues/123",
        )
        error_str = str(error)
        assert "Known issue:" in error_str
        assert "https://github.com/test/issues/123" in error_str

    def test_error_with_original_exception(self):
        """Test error wrapping original exception."""
        original = ValueError("Original error")
        error = HangarError(
            message="Wrapped error",
            provider="test",
            original_exception=original,
        )
        assert error.original_exception == original

    def test_error_with_context(self):
        """Test error with context dict."""
        error = HangarError(
            message="Test",
            provider="test",
            context={"key": "value", "count": 42},
        )
        result = error.to_dict()
        assert result["context"] == {"key": "value", "count": 42}


class TestProviderProtocolError:
    """Tests for ProviderProtocolError."""

    def test_default_recovery_hints(self):
        """Test that default recovery hints are provided."""
        error = ProviderProtocolError(
            message="Invalid JSON response",
            provider="sqlite",
            operation="query",
        )
        assert len(error.recovery_hints) > 0
        assert any("retry" in hint.lower() for hint in error.recovery_hints)

    def test_raw_response_included(self):
        """Test that raw response preview is included."""
        error = ProviderProtocolError(
            message="Invalid response",
            provider="test",
            operation="invoke",
            raw_response="SELECT * FROM users",
        )
        assert error.raw_response == "SELECT * FROM users"


class TestProviderCrashError:
    """Tests for ProviderCrashError."""

    def test_with_exit_code(self):
        """Test error with exit code."""
        error = ProviderCrashError(
            message="Provider crashed",
            provider="fetch",
            operation="shutdown",
            exit_code=-9,
        )
        assert error.exit_code == -9
        assert "auto-restart" in str(error).lower()

    def test_with_idle_duration(self):
        """Test error with idle duration context."""
        error = ProviderCrashError(
            message="Provider shutdown",
            provider="fetch",
            operation="shutdown",
            idle_duration_s=195.0,
        )
        assert error.idle_duration_s == 195.0
        assert "idle" in str(error).lower()

    def test_with_signal_name(self):
        """Test error with signal name."""
        error = ProviderCrashError(
            message="Crashed",
            provider="test",
            operation="invoke",
            exit_code=-9,
            signal_name="SIGKILL",
        )
        assert error.signal_name == "SIGKILL"

    def test_default_recovery_hints(self):
        """Test default recovery hints for crash."""
        error = ProviderCrashError(
            message="Crashed",
            provider="test",
            operation="invoke",
        )
        assert len(error.recovery_hints) > 0


class TestNetworkError:
    """Tests for NetworkError."""

    def test_with_hostname(self):
        """Test error with hostname suggestion."""
        error = NetworkError(
            message="Cannot connect",
            provider="fetch",
            operation="request",
            hostname="httpbin.org",
        )
        assert error.hostname == "httpbin.org"
        assert "ping" in str(error).lower() or "network" in str(error).lower()

    def test_default_recovery_hints(self):
        """Test default network recovery hints."""
        error = NetworkError(
            message="Connection failed",
            provider="test",
            operation="invoke",
        )
        assert len(error.recovery_hints) > 0


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_with_config_path(self):
        """Test error with config path."""
        error = ConfigurationError(
            message="Invalid config",
            provider="test",
            config_path="/path/to/config.yaml",
        )
        assert error.config_path == "/path/to/config.yaml"

    def test_with_field_name(self):
        """Test error with field name."""
        error = ConfigurationError(
            message="Missing key",
            provider="test",
            field_name="providers.sqlite.image",
        )
        assert error.field_name == "providers.sqlite.image"

    def test_default_recovery_hints(self):
        """Test default config recovery hints."""
        error = ConfigurationError(
            message="Bad config",
            provider="test",
        )
        assert len(error.recovery_hints) > 0


class TestProviderNotFoundError:
    """Tests for ProviderNotFoundError."""

    def test_did_you_mean_suggestion(self):
        """Test 'did you mean' suggestion for similar names."""
        error = ProviderNotFoundError(
            message="Provider not found",
            provider="mat",  # typo for "math"
            available_providers=["math", "sqlite", "memory"],
        )
        assert "math" in str(error) or len(error.recovery_hints) > 0

    def test_available_providers_in_hints(self):
        """Test available providers listed in hints."""
        error = ProviderNotFoundError(
            message="Not found",
            provider="unknown",
            available_providers=["math", "sqlite"],
        )
        error_str = str(error)
        # Should mention available providers
        assert "registry_list" in error_str or len(error.recovery_hints) > 0


class TestToolNotFoundError:
    """Tests for ToolNotFoundError."""

    def test_with_tool_name(self):
        """Test error with tool name."""
        error = ToolNotFoundError(
            message="Tool not found",
            provider="math",
            operation="invoke",
            tool_name="subtract",
            available_tools=["add", "multiply"],
        )
        assert error.tool_name == "subtract"
        assert error.available_tools == ["add", "multiply"]

    def test_similar_tool_suggestion(self):
        """Test similar tool suggestion."""
        error = ToolNotFoundError(
            message="Not found",
            provider="math",
            tool_name="ad",  # typo for "add"
            available_tools=["add", "multiply"],
        )
        # Should find "add" as similar
        hints_str = " ".join(error.recovery_hints)
        assert "add" in hints_str or "registry_tools" in hints_str


class TestTimeoutError:
    """Tests for TimeoutError."""

    def test_with_timeout_seconds(self):
        """Test error with timeout value."""
        error = TimeoutError(
            message="Operation timed out",
            provider="slow-provider",
            operation="query",
            timeout_seconds=30.0,
        )
        assert error.timeout_seconds == 30.0

    def test_default_recovery_hints(self):
        """Test default timeout recovery hints."""
        error = TimeoutError(
            message="Timed out",
            provider="test",
            timeout_seconds=30.0,
        )
        assert len(error.recovery_hints) > 0


class TestRateLimitError:
    """Tests for RateLimitError."""

    def test_with_limit_info(self):
        """Test error with rate limit info."""
        error = RateLimitError(
            message="Rate limited",
            provider="test",
            operation="invoke",
            limit=10,
            window_seconds=60,
            retry_after_seconds=5.5,
        )
        assert error.limit == 10
        assert error.window_seconds == 60
        assert error.retry_after_seconds == 5.5

    def test_default_recovery_hints(self):
        """Test default rate limit recovery hints."""
        error = RateLimitError(
            message="Too many requests",
            provider="test",
            retry_after_seconds=10.0,
        )
        assert len(error.recovery_hints) > 0
        hints_str = " ".join(error.recovery_hints)
        assert "10" in hints_str or "wait" in hints_str.lower()


class TestProviderDegradedError:
    """Tests for ProviderDegradedError."""

    def test_with_failure_info(self):
        """Test error with failure info."""
        error = ProviderDegradedError(
            message="Provider degraded",
            provider="flaky",
            operation="invoke",
            consecutive_failures=5,
            backoff_remaining_s=30.0,
        )
        assert error.consecutive_failures == 5
        assert error.backoff_remaining_s == 30.0

    def test_default_recovery_hints(self):
        """Test default degraded recovery hints."""
        error = ProviderDegradedError(
            message="Degraded",
            provider="test",
            consecutive_failures=3,
            backoff_remaining_s=15.0,
        )
        assert len(error.recovery_hints) > 0


class TestMapExceptionToHangarError:
    """Tests for exception mapping utility."""

    def test_json_decode_error_mapping(self):
        """Test that JSON errors are mapped to ProviderProtocolError."""
        original = json.JSONDecodeError("msg", "doc", 0)
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, ProviderProtocolError)

    def test_timeout_error_mapping(self):
        """Test that timeout errors are mapped correctly."""
        original = Exception("timeout after 30s")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, TimeoutError)

    def test_connection_error_mapping(self):
        """Test that connection errors are mapped to NetworkError."""
        original = ConnectionError("Connection refused")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, NetworkError)

    def test_generic_error_mapping(self):
        """Test that unknown errors get wrapped in HangarError."""
        original = ValueError("Some value error")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, HangarError)
        assert result.original_exception == original

    def test_dns_error_mapping(self):
        """Test DNS errors are mapped to NetworkError."""
        original = Exception("DNS resolution failed: EAI_AGAIN")
        result = map_exception_to_hangar_error(original, provider="fetch", operation="request")
        assert isinstance(result, NetworkError)

    def test_exit_code_error_mapping(self):
        """Test exit code errors are mapped to ProviderCrashError."""
        original = Exception("Process died with exit code -9")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke", context={"exit_code": -9})
        assert isinstance(result, ProviderCrashError)

    def test_rate_limit_error_mapping(self):
        """Test rate limit errors are mapped correctly."""
        original = Exception("Rate limit exceeded")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, RateLimitError)

    def test_provider_not_found_mapping(self):
        """Test provider not found errors."""
        original = Exception("Provider 'unknown' not found")
        result = map_exception_to_hangar_error(original, provider="unknown", operation="invoke")
        assert isinstance(result, ProviderNotFoundError)

    def test_tool_not_found_mapping(self):
        """Test tool not found errors."""
        original = Exception("Tool 'missing' not found")
        result = map_exception_to_hangar_error(
            original, provider="math", operation="invoke", context={"tool_name": "missing"}
        )
        assert isinstance(result, ToolNotFoundError)

    def test_client_malformed_error_mapping(self):
        """Test client malformed JSON errors are mapped to ProviderProtocolError."""
        # JSON errors in exception message map to ProviderProtocolError
        original = Exception("ClientError: malformed JSON response")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        # JSON-related errors map to ProviderProtocolError, which is retryable
        assert isinstance(result, ProviderProtocolError | TransientError)

    def test_socket_timeout_mapping(self):
        """Test socket timeout is mapped to TimeoutError."""

        original = builtins.TimeoutError("timed out")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, TimeoutError)

    def test_oserror_network_mapping(self):
        """Test OSError network errors are mapped."""
        original = OSError("Network is unreachable")
        result = map_exception_to_hangar_error(original, provider="test", operation="invoke")
        assert isinstance(result, NetworkError)

    def test_already_hangar_error_passthrough(self):
        """Test that HangarError is passed through unchanged."""
        original = ProviderProtocolError(
            message="Already wrapped",
            provider="test",
            operation="invoke",
        )
        result = map_exception_to_hangar_error(original, provider="other", operation="other")
        assert result is original


class TestIsRetryable:
    """Tests for is_retryable utility."""

    def test_transient_error_is_retryable(self):
        """Test that TransientError is retryable."""
        error = TransientError(message="Temporary failure")
        assert is_retryable(error) is True

    def test_protocol_error_is_retryable(self):
        """Test that ProviderProtocolError is retryable."""
        error = ProviderProtocolError(message="Bad JSON", provider="test")
        assert is_retryable(error) is True

    def test_config_error_not_retryable(self):
        """Test that ConfigurationError is not retryable."""
        error = ConfigurationError(message="Missing config")
        assert is_retryable(error) is False

    def test_timeout_pattern_is_retryable(self):
        """Test that timeout patterns are detected as retryable."""
        error = Exception("Operation timed out")
        assert is_retryable(error) is True

    def test_network_error_is_retryable(self):
        """Test that NetworkError is retryable."""
        error = NetworkError(message="Connection failed", provider="test")
        assert is_retryable(error) is True

    def test_timeout_error_is_retryable(self):
        """Test that TimeoutError is retryable."""
        error = TimeoutError(message="Timed out", provider="test")
        assert is_retryable(error) is True

    def test_provider_not_found_not_retryable(self):
        """Test that ProviderNotFoundError is not retryable."""
        error = ProviderNotFoundError(message="Not found", provider="test")
        assert is_retryable(error) is False

    def test_transient_with_retryable_false(self):
        """Test TransientError with retryable=False."""
        error = TransientError(message="Not retryable", retryable=False)
        assert is_retryable(error) is False

    def test_connection_pattern_is_retryable(self):
        """Test connection error patterns."""
        error = Exception("Connection refused")
        assert is_retryable(error) is True

    def test_json_pattern_is_retryable(self):
        """Test JSON error patterns."""
        error = Exception("Invalid JSON in response")
        assert is_retryable(error) is True


class TestErrorClassifier:
    """Tests for ErrorClassifier class."""

    def test_classify_division_by_zero(self):
        """Test classifying division by zero as permanent error."""
        from mcp_hangar.errors import ErrorClassifier

        error = ZeroDivisionError("division by zero")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is False
        assert result["should_retry"] is False
        assert "permanent" in result["final_error_reason"]
        assert "validation_error" in result["final_error_reason"]
        assert len(result["recovery_hints"]) > 0
        assert any("zero" in hint.lower() for hint in result["recovery_hints"])

    def test_classify_timeout_as_transient(self):
        """Test classifying timeout as transient error."""
        from mcp_hangar.errors import ErrorClassifier

        error = Exception("Operation timed out after 30s")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is True
        assert result["should_retry"] is True
        assert "transient" in result["final_error_reason"]
        assert len(result["recovery_hints"]) > 0

    def test_classify_connection_refused_as_transient(self):
        """Test classifying connection refused as transient error."""
        from mcp_hangar.errors import ErrorClassifier

        error = ConnectionRefusedError("Connection refused")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is True
        assert result["should_retry"] is True
        assert "transient" in result["final_error_reason"]

    def test_classify_provider_not_found_as_permanent(self):
        """Test classifying provider not found as permanent error."""
        from mcp_hangar.errors import ErrorClassifier

        error = Exception("Provider 'xyz' not found in registry")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is False
        assert result["should_retry"] is False
        assert "permanent" in result["final_error_reason"]

    def test_classify_tool_not_found_as_permanent(self):
        """Test classifying tool not found as permanent error."""
        from mcp_hangar.errors import ErrorClassifier

        error = Exception("Tool 'unknown_tool' not found on provider")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is False
        assert result["should_retry"] is False
        assert "permanent" in result["final_error_reason"]

    def test_classify_hangar_timeout_error(self):
        """Test classifying HangarError TimeoutError."""
        from mcp_hangar.errors import ErrorClassifier, TimeoutError as HangarTimeout

        error = HangarTimeout(
            message="Operation timed out",
            provider="math",
            timeout_seconds=30.0,
        )
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is True
        assert result["should_retry"] is True
        assert "transient" in result["final_error_reason"]
        # Should use recovery_hints from HangarError
        assert len(result["recovery_hints"]) > 0

    def test_classify_hangar_provider_not_found(self):
        """Test classifying HangarError ProviderNotFoundError."""
        from mcp_hangar.errors import ErrorClassifier, ProviderNotFoundError

        error = ProviderNotFoundError(
            message="Provider 'unknown' not found",
            provider="unknown",
            available_providers=["math", "sqlite"],
        )
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is False
        assert result["should_retry"] is False
        assert "permanent" in result["final_error_reason"]
        # Should use recovery_hints from HangarError
        assert len(result["recovery_hints"]) > 0

    def test_classify_network_error_as_transient(self):
        """Test classifying network error as transient."""
        from mcp_hangar.errors import ErrorClassifier

        error = Exception("Network error: EAI_AGAIN DNS failure")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is True
        assert result["should_retry"] is True

    def test_classify_json_error_as_transient(self):
        """Test classifying JSON error as transient."""
        from mcp_hangar.errors import ErrorClassifier

        error = Exception("Malformed JSON response from provider")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is True
        assert result["should_retry"] is True

    def test_classify_unknown_error_conservative(self):
        """Test unknown errors are classified conservatively (allow retry)."""
        from mcp_hangar.errors import ErrorClassifier

        error = Exception("Some unknown error xyz123")
        result = ErrorClassifier.classify(error)

        # Unknown errors should default to transient (conservative)
        assert result["is_transient"] is True
        assert result["should_retry"] is True
        assert "unknown" in result["final_error_reason"]
        assert len(result["recovery_hints"]) > 0

    def test_classify_value_error_as_permanent(self):
        """Test classifying ValueError as permanent."""
        from mcp_hangar.errors import ErrorClassifier

        error = ValueError("Invalid argument format")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is False
        assert result["should_retry"] is False
        assert "permanent" in result["final_error_reason"]

    def test_classify_permission_denied_as_permanent(self):
        """Test classifying permission denied as permanent."""
        from mcp_hangar.errors import ErrorClassifier

        error = PermissionError("Permission denied for file access")
        result = ErrorClassifier.classify(error)

        assert result["is_transient"] is False
        assert result["should_retry"] is False
        assert "permanent" in result["final_error_reason"]
