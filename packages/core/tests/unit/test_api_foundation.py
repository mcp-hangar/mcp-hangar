"""Tests for the REST API foundation module.

Tests cover error handling middleware, dispatch helpers, serializers,
CORS configuration, and the JSON response class.

All tests are unit tests that mock external dependencies.
"""

import json
import os
from datetime import datetime, UTC
from enum import Enum
from unittest.mock import Mock, patch

import pytest
from starlette.requests import Request

from mcp_hangar.domain.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    MCPError,
    ProviderDegradedError,
    ProviderNotFoundError,
    ProviderNotReadyError,
    RateLimitExceeded,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Error handler tests
# ---------------------------------------------------------------------------


class TestErrorHandler:
    """Tests for error handler mapping domain exceptions to HTTP status codes."""

    def _make_scope(self, path: str = "/test") -> dict:
        """Create minimal ASGI scope for request construction."""
        return {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [],
        }

    @pytest.mark.asyncio
    async def test_provider_not_found_returns_404(self):
        """ProviderNotFoundError maps to HTTP 404."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ProviderNotFoundError("test-provider")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_404(self):
        """ToolNotFoundError maps to HTTP 404."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ToolNotFoundError("test-provider", "test-tool")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_validation_error_returns_422(self):
        """ValidationError maps to HTTP 422."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ValidationError("Invalid field", field="name")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429(self):
        """RateLimitExceeded maps to HTTP 429."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = RateLimitExceeded(provider_id="test", limit=100, window_seconds=60)
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_authentication_error_returns_401(self):
        """AuthenticationError maps to HTTP 401."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = AuthenticationError("Not authenticated")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_access_denied_error_returns_403(self):
        """AccessDeniedError maps to HTTP 403."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = AccessDeniedError("user1", "write", "provider")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_provider_degraded_returns_503(self):
        """ProviderDegradedError maps to HTTP 503."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ProviderDegradedError("test-provider", backoff_remaining=5.0)
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_tool_timeout_returns_504(self):
        """ToolTimeoutError maps to HTTP 504."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ToolTimeoutError("test-provider", "test-tool", timeout=30.0)
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 504

    @pytest.mark.asyncio
    async def test_provider_not_ready_returns_409(self):
        """ProviderNotReadyError maps to HTTP 409."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ProviderNotReadyError("test-provider", current_state="initializing")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_generic_mcp_error_returns_500(self):
        """Generic MCPError maps to HTTP 500."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = MCPError("Something went wrong")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_500(self):
        """Unhandled Exception maps to HTTP 500."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = RuntimeError("Unexpected failure")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception_does_not_leak_internals(self):
        """Unhandled Exception does not expose internal error message."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = RuntimeError("Secret internal detail: password=abc123")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)

        body = json.loads(response.body)
        # The internal message should not appear in response
        assert "password" not in json.dumps(body)
        assert "abc123" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_error_envelope_format(self):
        """Error response follows {error: {code, message, details}} format."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ProviderNotFoundError("my-provider")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)

        body = json.loads(response.body)
        assert "error" in body
        error = body["error"]
        assert "code" in error
        assert "message" in error
        assert "details" in error

    @pytest.mark.asyncio
    async def test_error_code_is_exception_class_name(self):
        """Error code equals exception class name."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ProviderNotFoundError("my-provider")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)

        body = json.loads(response.body)
        assert body["error"]["code"] == "ProviderNotFoundError"

    @pytest.mark.asyncio
    async def test_error_message_contains_exception_message(self):
        """Error message contains the exception message."""
        from mcp_hangar.server.api.middleware import error_handler

        exc = ProviderNotFoundError("my-provider")
        scope = self._make_scope()
        request = Request(scope)
        response = await error_handler(request, exc)

        body = json.loads(response.body)
        assert "my-provider" in body["error"]["message"]


# ---------------------------------------------------------------------------
# Dispatch helper tests
# ---------------------------------------------------------------------------


class TestDispatchHelpers:
    """Tests for dispatch_query and dispatch_command helpers."""

    @pytest.mark.asyncio
    async def test_dispatch_query_uses_run_in_threadpool(self):
        """dispatch_query calls run_in_threadpool with query_bus.execute."""
        from mcp_hangar.server.api.middleware import dispatch_query

        mock_query = Mock()
        mock_result = Mock()
        mock_query_bus = Mock()
        mock_query_bus.execute.return_value = mock_result

        mock_context = Mock()
        mock_context.query_bus = mock_query_bus

        with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
            with patch("mcp_hangar.server.api.middleware.run_in_threadpool") as mock_threadpool:
                mock_threadpool.return_value = mock_result
                # run_in_threadpool is async, so make it awaitable
                mock_threadpool.return_value = mock_result

                async def fake_threadpool(func, *args, **kwargs):
                    return func(*args, **kwargs)

                mock_threadpool.side_effect = fake_threadpool

                result = await dispatch_query(mock_query)
                mock_threadpool.assert_called_once()
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_dispatch_command_uses_run_in_threadpool(self):
        """dispatch_command calls run_in_threadpool with command_bus.send."""
        from mcp_hangar.server.api.middleware import dispatch_command

        mock_command = Mock()
        mock_result = Mock()
        mock_command_bus = Mock()
        mock_command_bus.send.return_value = mock_result

        mock_context = Mock()
        mock_context.command_bus = mock_command_bus

        with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
            with patch("mcp_hangar.server.api.middleware.run_in_threadpool") as mock_threadpool:

                async def fake_threadpool(func, *args, **kwargs):
                    return func(*args, **kwargs)

                mock_threadpool.side_effect = fake_threadpool

                result = await dispatch_command(mock_command)
                mock_threadpool.assert_called_once()
                assert result == mock_result

    @pytest.mark.asyncio
    async def test_dispatch_query_returns_query_bus_result(self):
        """dispatch_query returns the result from query_bus.execute."""
        from mcp_hangar.server.api.middleware import dispatch_query

        expected_result = {"providers": ["a", "b"]}
        mock_query_bus = Mock()
        mock_query_bus.execute.return_value = expected_result

        mock_context = Mock()
        mock_context.query_bus = mock_query_bus

        with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
            result = await dispatch_query(Mock())

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_dispatch_command_returns_command_bus_result(self):
        """dispatch_command returns the result from command_bus.send."""
        from mcp_hangar.server.api.middleware import dispatch_command

        expected_result = {"status": "started"}
        mock_command_bus = Mock()
        mock_command_bus.send.return_value = expected_result

        mock_context = Mock()
        mock_context.command_bus = mock_command_bus

        with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
            result = await dispatch_command(Mock())

        assert result == expected_result


# ---------------------------------------------------------------------------
# HangarJSONResponse and HangarJSONEncoder tests
# ---------------------------------------------------------------------------


class TestHangarJSONResponse:
    """Tests for HangarJSONResponse and HangarJSONEncoder."""

    def test_handles_datetime_as_iso_string(self):
        """HangarJSONEncoder serializes datetime to ISO 8601 string."""
        from mcp_hangar.server.api.serializers import HangarJSONEncoder

        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = json.dumps({"ts": dt}, cls=HangarJSONEncoder)
        data = json.loads(result)
        assert "2025-01-15" in data["ts"]
        assert "10:30:00" in data["ts"]

    def test_handles_enum_as_value(self):
        """HangarJSONEncoder serializes Enum to its .value."""
        from mcp_hangar.server.api.serializers import HangarJSONEncoder

        class Color(Enum):
            RED = "red"
            BLUE = "blue"

        result = json.dumps({"color": Color.RED}, cls=HangarJSONEncoder)
        data = json.loads(result)
        assert data["color"] == "red"

    def test_handles_set_as_list(self):
        """HangarJSONEncoder serializes set to list."""
        from mcp_hangar.server.api.serializers import HangarJSONEncoder

        result = json.dumps({"items": {1, 2, 3}}, cls=HangarJSONEncoder)
        data = json.loads(result)
        assert isinstance(data["items"], list)
        assert sorted(data["items"]) == [1, 2, 3]

    def test_handles_to_dict_objects(self):
        """HangarJSONEncoder calls .to_dict() on objects that have it."""
        from mcp_hangar.server.api.serializers import HangarJSONEncoder

        class MyObj:
            def to_dict(self) -> dict:
                return {"key": "value"}

        result = json.dumps({"obj": MyObj()}, cls=HangarJSONEncoder)
        data = json.loads(result)
        assert data["obj"] == {"key": "value"}

    def test_hangar_json_response_uses_custom_encoder(self):
        """HangarJSONResponse properly serializes complex objects."""
        from mcp_hangar.server.api.serializers import HangarJSONResponse

        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)
        response = HangarJSONResponse({"timestamp": dt})
        body = json.loads(response.body)
        assert "2025-01-15" in body["timestamp"]

    def test_hangar_json_response_is_json_response(self):
        """HangarJSONResponse is a subclass of JSONResponse."""
        from starlette.responses import JSONResponse

        from mcp_hangar.server.api.serializers import HangarJSONResponse

        response = HangarJSONResponse({"status": "ok"})
        assert isinstance(response, JSONResponse)


# ---------------------------------------------------------------------------
# Serializer function tests
# ---------------------------------------------------------------------------


class TestSerializers:
    """Tests for provider object serializers."""

    def test_serialize_provider_summary(self):
        """serialize_provider_summary converts ProviderSummary to dict."""
        from mcp_hangar.application.read_models.provider_views import ProviderSummary
        from mcp_hangar.server.api.serializers import serialize_provider_summary

        summary = ProviderSummary(
            provider_id="test",
            state="ready",
            mode="subprocess",
            is_alive=True,
            tools_count=3,
            health_status="healthy",
        )
        result = serialize_provider_summary(summary)
        assert isinstance(result, dict)
        assert result["provider_id"] == "test"
        assert result["state"] == "ready"
        assert result["tools_count"] == 3

    def test_serialize_provider_details(self):
        """serialize_provider_details converts ProviderDetails to dict."""
        from mcp_hangar.application.read_models.provider_views import (
            HealthInfo,
            ProviderDetails,
            ToolInfo,
        )
        from mcp_hangar.server.api.serializers import serialize_provider_details

        tool = ToolInfo(
            name="my_tool",
            description="A tool",
            input_schema={"type": "object"},
        )
        health = HealthInfo(
            consecutive_failures=0,
            total_invocations=10,
            total_failures=1,
            success_rate=0.9,
            can_retry=True,
        )
        details = ProviderDetails(
            provider_id="test",
            state="ready",
            mode="subprocess",
            is_alive=True,
            tools=[tool],
            health=health,
            idle_time=0.0,
        )
        result = serialize_provider_details(details)
        assert isinstance(result, dict)
        assert result["provider_id"] == "test"
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "my_tool"
        assert "health" in result


# ---------------------------------------------------------------------------
# CORS configuration tests
# ---------------------------------------------------------------------------


class TestCORSConfig:
    """Tests for CORS configuration."""

    def test_get_cors_config_reads_env_var(self):
        """get_cors_config reads MCP_CORS_ORIGINS from env var."""
        from mcp_hangar.server.api.middleware import get_cors_config

        with patch.dict(os.environ, {"MCP_CORS_ORIGINS": "http://example.com,http://other.com"}):
            config = get_cors_config()

        assert "http://example.com" in config["allow_origins"]
        assert "http://other.com" in config["allow_origins"]

    def test_get_cors_config_defaults_when_not_set(self):
        """get_cors_config defaults when MCP_CORS_ORIGINS is not set."""
        from mcp_hangar.server.api.middleware import get_cors_config

        env = {k: v for k, v in os.environ.items() if k != "MCP_CORS_ORIGINS"}
        with patch.dict(os.environ, env, clear=True):
            config = get_cors_config()

        # Should have some default origins, not an empty list
        assert isinstance(config["allow_origins"], list)
        assert len(config["allow_origins"]) > 0

    def test_get_cors_config_includes_all_methods(self):
        """get_cors_config includes allow_methods=['*']."""
        from mcp_hangar.server.api.middleware import get_cors_config

        config = get_cors_config()
        assert config.get("allow_methods") == ["*"]

    def test_get_cors_config_includes_all_headers(self):
        """get_cors_config includes allow_headers=['*']."""
        from mcp_hangar.server.api.middleware import get_cors_config

        config = get_cors_config()
        assert config.get("allow_headers") == ["*"]
