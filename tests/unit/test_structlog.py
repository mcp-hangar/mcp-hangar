"""Tests for structlog logging configuration."""

import json
import logging

import pytest
import structlog

from mcp_hangar.context import (
    bind_request_context,
    clear_request_context,
    generate_request_id,
    get_request_id,
    RequestContextManager,
    update_request_context,
)
from mcp_hangar.logging_config import _add_service_context, _sanitize_sensitive_data, get_logger, setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    def teardown_method(self):
        """Reset logging state after each test."""
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    def test_setup_logging_development_mode(self):
        """Test development mode logging setup."""
        setup_logging(level="DEBUG", json_format=False, development=True)
        logger = get_logger("test")
        # Should not raise
        logger.info("test_message", key="value")

    def test_setup_logging_production_mode(self):
        """Test production mode (JSON) logging setup."""
        setup_logging(level="INFO", json_format=True)
        logger = get_logger("test")
        # Should not raise
        logger.info("test_message", key="value")

    def test_setup_logging_level_filtering(self, capsys):
        """Test that log level filtering works."""
        setup_logging(level="WARNING", json_format=False)
        logger = get_logger("test")

        logger.info("should_not_appear")
        logger.warning("should_appear")

        captured = capsys.readouterr()
        assert "should_not_appear" not in captured.err
        assert "should_appear" in captured.err


class TestSensitiveDataSanitization:
    """Tests for sensitive data redaction."""

    def test_password_redacted(self):
        """Test that password fields are redacted."""
        event_dict = {"password": "secret123", "user": "admin"}
        result = _sanitize_sensitive_data(None, "info", event_dict)
        assert result["password"] == "[REDACTED]"
        assert result["user"] == "admin"

    def test_api_key_redacted(self):
        """Test that API key fields are redacted."""
        event_dict = {"api_key": "sk-12345", "name": "test"}
        result = _sanitize_sensitive_data(None, "info", event_dict)
        assert result["api_key"] == "[REDACTED]"
        assert result["name"] == "test"

    def test_nested_sensitive_data_redacted(self):
        """Test that nested sensitive fields are redacted."""
        event_dict = {
            "config": {
                "token": "bearer-xyz",
                "host": "localhost",
            }
        }
        result = _sanitize_sensitive_data(None, "info", event_dict)
        assert result["config"]["token"] == "[REDACTED]"
        assert result["config"]["host"] == "localhost"

    def test_list_with_sensitive_data(self):
        """Test that sensitive data in lists is redacted."""
        event_dict = {
            "credentials": [
                {"password": "pass1"},
                {"password": "pass2"},
            ]
        }
        result = _sanitize_sensitive_data(None, "info", event_dict)
        assert result["credentials"][0]["password"] == "[REDACTED]"
        assert result["credentials"][1]["password"] == "[REDACTED]"


class TestServiceContext:
    """Tests for service context injection."""

    def test_adds_service_name(self):
        """Test that service name is added to log entries."""
        event_dict = {"event": "test"}
        result = _add_service_context(None, "info", event_dict)
        assert result["service"] == "mcp-hangar"

    def test_does_not_override_existing_service(self):
        """Test that existing service name is not overridden."""
        event_dict = {"event": "test", "service": "custom-service"}
        result = _add_service_context(None, "info", event_dict)
        assert result["service"] == "custom-service"


class TestRequestContext:
    """Tests for request context management."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_request_context()

    def test_generate_request_id_unique(self):
        """Test that generated request IDs are unique."""
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100

    def test_generate_request_id_format(self):
        """Test request ID format (12 hex characters)."""
        request_id = generate_request_id()
        assert len(request_id) == 12
        assert all(c in "0123456789abcdef" for c in request_id)

    def test_bind_request_context_auto_id(self):
        """Test that request ID is auto-generated if not provided."""
        bind_request_context()
        request_id = get_request_id()
        assert request_id is not None
        assert len(request_id) == 12

    def test_bind_request_context_custom_id(self):
        """Test binding with custom request ID."""
        bind_request_context(request_id="custom-123")
        assert get_request_id() == "custom-123"

    def test_bind_request_context_with_metadata(self):
        """Test binding with additional metadata."""
        request_id = bind_request_context(
            server_name="filesystem",
            tool_name="read_file",
            custom_key="custom_value",
        )
        assert get_request_id() == request_id

    def test_clear_request_context(self):
        """Test clearing request context."""
        bind_request_context(
            request_id="test-id",
            server_name="test-server",
        )
        clear_request_context()
        assert get_request_id() is None

    def test_update_request_context(self):
        """Test updating request context with additional data."""
        bind_request_context()
        update_request_context(new_key="new_value")
        # Context should still have request_id
        assert get_request_id() is not None


class TestRequestContextManager:
    """Tests for RequestContextManager."""

    def test_context_manager_binds_context(self):
        """Test that context manager binds context on entry."""
        with RequestContextManager(tool_name="test_tool") as ctx:
            assert ctx.request_id is not None
            assert get_request_id() == ctx.request_id

    def test_context_manager_clears_context(self):
        """Test that context manager clears context on exit."""
        with RequestContextManager(tool_name="test_tool"):
            pass
        assert get_request_id() is None

    def test_context_manager_clears_on_exception(self):
        """Test that context is cleared even on exception."""
        try:
            with RequestContextManager(tool_name="test_tool"):
                raise ValueError("test error")
        except ValueError:
            pass
        assert get_request_id() is None

    def test_context_manager_with_custom_id(self):
        """Test context manager with custom request ID."""
        with RequestContextManager(request_id="custom-id") as ctx:
            assert ctx.request_id == "custom-id"
            assert get_request_id() == "custom-id"

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Test async context manager support."""
        async with RequestContextManager(tool_name="async_test") as ctx:
            assert ctx.request_id is not None
            assert get_request_id() == ctx.request_id
        assert get_request_id() is None


class TestLoggerIntegration:
    """Integration tests for logging with context."""

    def teardown_method(self):
        """Reset state after each test."""
        clear_request_context()
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    def test_log_includes_request_context(self, capsys):
        """Test that logs include bound request context."""
        setup_logging(level="INFO", json_format=True)
        logger = get_logger("test")

        bind_request_context(
            request_id="test-req-123",
            server_name="test-server",
        )

        logger.info("test_event", custom="data")

        captured = capsys.readouterr()
        log_line = captured.err.strip()
        log_data = json.loads(log_line)

        assert log_data["request_id"] == "test-req-123"
        assert log_data["server"] == "test-server"
        assert log_data["event"] == "test_event"
        assert log_data["custom"] == "data"

    def test_context_cleared_between_requests(self, capsys):
        """Test that context from one request doesn't leak to another."""
        setup_logging(level="INFO", json_format=True)
        logger = get_logger("test")

        # First request
        bind_request_context(request_id="req-1", server_name="server-1")
        logger.info("first_request")
        clear_request_context()

        # Second request
        bind_request_context(request_id="req-2")
        logger.info("second_request")

        captured = capsys.readouterr()
        lines = captured.err.strip().split("\n")

        first_log = json.loads(lines[0])
        second_log = json.loads(lines[1])

        assert first_log["request_id"] == "req-1"
        assert first_log["server"] == "server-1"

        assert second_log["request_id"] == "req-2"
        assert "server" not in second_log
