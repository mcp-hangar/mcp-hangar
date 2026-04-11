"""Tests for HTTP client and HTTP transport layer.

Tests cover:
- HttpClient initialization and configuration
- Authentication schemes (none, api_key, bearer, basic)
- SSE streaming support
- Connection handling and lifecycle
- Error handling and retries
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.http_client import AuthConfig, AuthType, HttpClient, HttpClientConfig


class TestAuthConfig:
    """Tests for AuthConfig value object."""

    def test_no_auth_default(self):
        """Default auth config should be no auth."""
        auth = AuthConfig()
        assert auth.auth_type == AuthType.NONE
        assert auth.get_headers() == {}

    def test_api_key_auth(self):
        """API key auth should produce correct header."""
        auth = AuthConfig(
            auth_type=AuthType.API_KEY,
            api_key="test-key-123",
            api_key_header="X-API-Key",
        )
        headers = auth.get_headers()
        assert headers == {"X-API-Key": "test-key-123"}

    def test_api_key_custom_header(self):
        """API key auth should support custom header name."""
        auth = AuthConfig(
            auth_type=AuthType.API_KEY,
            api_key="my-secret",
            api_key_header="Authorization-Key",
        )
        headers = auth.get_headers()
        assert headers == {"Authorization-Key": "my-secret"}

    def test_bearer_auth(self):
        """Bearer auth should produce Authorization header."""
        auth = AuthConfig(
            auth_type=AuthType.BEARER,
            bearer_token="jwt-token-here",
        )
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer jwt-token-here"}

    def test_basic_auth(self):
        """Basic auth should produce base64 encoded Authorization header."""
        auth = AuthConfig(
            auth_type=AuthType.BASIC,
            basic_username="user",
            basic_password="pass",
        )
        headers = auth.get_headers()
        # "user:pass" base64 encoded is "dXNlcjpwYXNz"
        assert headers == {"Authorization": "Basic dXNlcjpwYXNz"}

    def test_api_key_requires_key(self):
        """API key auth should require api_key."""
        with pytest.raises(ValueError, match="api_key is required"):
            AuthConfig(auth_type=AuthType.API_KEY)

    def test_bearer_requires_token(self):
        """Bearer auth should require bearer_token."""
        with pytest.raises(ValueError, match="bearer_token is required"):
            AuthConfig(auth_type=AuthType.BEARER)

    def test_basic_requires_credentials(self):
        """Basic auth should require username and password."""
        with pytest.raises(ValueError, match="basic_username and basic_password"):
            AuthConfig(auth_type=AuthType.BASIC, basic_username="user")

        with pytest.raises(ValueError, match="basic_username and basic_password"):
            AuthConfig(auth_type=AuthType.BASIC, basic_password="pass")


class TestHttpClientConfig:
    """Tests for HttpClientConfig."""

    def test_default_config(self):
        """Default config should have sensible values."""
        config = HttpClientConfig()
        assert config.connect_timeout == 10.0
        assert config.read_timeout == 30.0
        assert config.max_retries == 3
        assert config.verify_ssl is True
        assert config.keep_alive is True

    def test_custom_config(self):
        """Should support custom configuration."""
        config = HttpClientConfig(
            connect_timeout=5.0,
            read_timeout=60.0,
            max_retries=5,
            verify_ssl=False,
            ca_cert_path="/path/to/ca.pem",
        )
        assert config.connect_timeout == 5.0
        assert config.read_timeout == 60.0
        assert config.max_retries == 5
        assert config.verify_ssl is False
        assert config.ca_cert_path == "/path/to/ca.pem"


class TestHttpClient:
    """Tests for HttpClient."""

    def test_endpoint_parsing_https(self):
        """Should correctly parse HTTPS endpoint."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://mcp.example.com:8443/api/v1")
            assert client._scheme == "https"
            assert client._host == "mcp.example.com"
            assert client._port == 8443
            assert client._base_path == "/api/v1"

    def test_endpoint_parsing_http_default_port(self):
        """Should use default port for HTTP."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("http://localhost/mcp")
            assert client._scheme == "http"
            assert client._host == "localhost"
            assert client._port == 80
            assert client._base_path == "/mcp"

    def test_endpoint_parsing_https_default_port(self):
        """Should use default port 443 for HTTPS."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://api.example.com")
            assert client._scheme == "https"
            assert client._host == "api.example.com"
            assert client._port == 443

    def test_build_headers_with_auth(self):
        """Should include auth headers in requests."""
        auth = AuthConfig(auth_type=AuthType.BEARER, bearer_token="test-token")

        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com", auth_config=auth)
            headers = client._build_headers()

            assert headers["Authorization"] == "Bearer test-token"
            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json, text/event-stream"

    def test_build_headers_with_custom_headers(self):
        """Should include custom headers from config."""
        http_config = HttpClientConfig(extra_headers={"X-Custom": "value", "X-Request-Id": "123"})

        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com", http_config=http_config)
            headers = client._build_headers()

            assert headers["X-Custom"] == "value"
            assert headers["X-Request-Id"] == "123"

    def test_is_alive_when_closed(self):
        """Should return False when client is closed."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com")
            client._closed = True
            assert client.is_alive() is False

    def test_call_when_closed(self):
        """Should raise ClientError when calling closed client."""
        from mcp_hangar.domain.exceptions import ClientError

        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com")
            client.close()

            with pytest.raises(ClientError, match="client_closed"):
                client.call("test/method", {})

    def test_call_json_response(self):
        """Should handle JSON response correctly."""
        mock_httpx_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": "test", "result": {"value": 42}}
        mock_httpx_client.post.return_value = mock_response

        with patch.object(HttpClient, "_create_client", return_value=mock_httpx_client):
            client = HttpClient("https://example.com")
            result = client.call("test/method", {"arg": 1})

            assert "result" in result
            assert result["result"]["value"] == 42

    def test_call_http_error(self):
        """Should return error for HTTP error responses."""
        mock_httpx_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = "Internal Server Error"
        mock_httpx_client.post.return_value = mock_response

        with patch.object(HttpClient, "_create_client", return_value=mock_httpx_client):
            client = HttpClient("https://example.com")
            result = client.call("test/method", {})

            assert "error" in result
            assert result["error"]["code"] == -32000
            assert "500" in result["error"]["message"]

    def test_close_is_idempotent(self):
        """Should be safe to call close multiple times."""
        mock_httpx_client = Mock()

        with patch.object(HttpClient, "_create_client", return_value=mock_httpx_client):
            client = HttpClient("https://example.com")
            client.close()
            client.close()  # Should not raise

            # Client should only be closed once
            assert mock_httpx_client.close.call_count == 1

    def test_context_manager(self):
        """Should support context manager protocol."""
        mock_httpx_client = Mock()

        with patch.object(HttpClient, "_create_client", return_value=mock_httpx_client):
            with HttpClient("https://example.com") as client:
                assert client.is_alive()

            # Should be closed after exiting context
            mock_httpx_client.close.assert_called_once()


class TestHttpClientSSE:
    """Tests for SSE streaming functionality."""

    def test_parse_sse_event_data(self):
        """Should parse SSE event with data field."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com")

            event_data = 'event: message\ndata: {"jsonrpc":"2.0","id":"test-id","result":{"ok":true}}'
            result = client._parse_sse_event(event_data, "test-id")

            assert result is not None
            assert result["id"] == "test-id"
            assert result["result"]["ok"] is True

    def test_parse_sse_event_wrong_id(self):
        """Should return None for events with different request ID."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com")

            event_data = 'data: {"jsonrpc":"2.0","id":"other-id","result":{}}'
            result = client._parse_sse_event(event_data, "my-id")

            assert result is None

    def test_parse_sse_event_invalid_json(self):
        """Should return None for invalid JSON in SSE event."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com")

            event_data = "data: {invalid json"
            result = client._parse_sse_event(event_data, "test-id")

            assert result is None

    def test_parse_sse_event_no_data(self):
        """Should return None for events without data field."""
        with patch.object(HttpClient, "_create_client", return_value=Mock()):
            client = HttpClient("https://example.com")

            event_data = "event: ping\n: comment"
            result = client._parse_sse_event(event_data, "test-id")

            assert result is None


class TestHttpLauncher:
    """Tests for HttpLauncher."""

    def test_validate_endpoint_valid_https(self):
        """Should accept valid HTTPS endpoint."""
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        launcher = HttpLauncher()
        # Should not raise
        launcher._validate_endpoint("https://mcp.example.com/api")

    def test_validate_endpoint_valid_http(self):
        """Should accept valid HTTP endpoint."""
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        launcher = HttpLauncher()
        launcher._validate_endpoint("http://localhost:8080")

    def test_validate_endpoint_missing_scheme(self):
        """Should reject endpoint without scheme."""
        from mcp_hangar.domain.exceptions import ValidationError
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        launcher = HttpLauncher()
        with pytest.raises(ValidationError, match="scheme"):
            launcher._validate_endpoint("mcp.example.com/api")

    def test_validate_endpoint_invalid_scheme(self):
        """Should reject endpoint with unsupported scheme."""
        from mcp_hangar.domain.exceptions import ValidationError
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        launcher = HttpLauncher()
        with pytest.raises(ValidationError, match="Unsupported endpoint scheme"):
            launcher._validate_endpoint("ftp://files.example.com")

    def test_validate_endpoint_empty(self):
        """Should reject empty endpoint."""
        from mcp_hangar.domain.exceptions import ValidationError
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        launcher = HttpLauncher()
        with pytest.raises(ValidationError, match="required"):
            launcher._validate_endpoint("")

    @patch("mcp_hangar.http_client.HttpClient")
    def test_launch_creates_http_client(self, mock_client_class):
        """Should create HttpClient with correct configuration."""
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        mock_client = Mock()
        mock_client_class.return_value = mock_client

        launcher = HttpLauncher()
        result = launcher.launch(
            endpoint="https://mcp.example.com",
            auth_config={"type": "bearer", "bearer_token": "token123"},
        )

        assert result == mock_client
        mock_client_class.assert_called_once()

    @patch("mcp_hangar.http_client.HttpClient")
    def test_launch_with_tls_config(self, mock_client_class):
        """Should pass TLS configuration to HttpClient."""
        from mcp_hangar.domain.services.provider_launcher import HttpLauncher

        mock_client = Mock()
        mock_client_class.return_value = mock_client

        launcher = HttpLauncher()
        launcher.launch(
            endpoint="https://mcp.example.com",
            tls_config={"verify_ssl": False, "ca_cert_path": "/path/to/ca.pem"},
        )

        # Verify the http_config passed has TLS settings
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["http_config"].verify_ssl is False
        assert call_kwargs["http_config"].ca_cert_path == "/path/to/ca.pem"


class TestGetLauncher:
    """Tests for get_launcher factory function."""

    def test_get_launcher_remote(self):
        """Should return HttpLauncher for remote mode."""
        from mcp_hangar.domain.services.provider_launcher import get_launcher, HttpLauncher

        launcher = get_launcher("remote")
        assert isinstance(launcher, HttpLauncher)

    def test_get_launcher_subprocess(self):
        """Should return SubprocessLauncher for subprocess mode."""
        from mcp_hangar.domain.services.provider_launcher import get_launcher, SubprocessLauncher

        launcher = get_launcher("subprocess")
        assert isinstance(launcher, SubprocessLauncher)

    def test_get_launcher_invalid_mode(self):
        """Should raise ValueError for invalid mode."""
        from mcp_hangar.domain.services.provider_launcher import get_launcher

        with pytest.raises(ValueError, match="unsupported_mode"):
            get_launcher("invalid_mode")
