"""Integration tests for HTTP transport with remote MCP providers.

Tests cover:
- Provider configuration with remote mode
- Connection to HTTP MCP endpoints
- Authentication schemes in real scenarios
- Health checks for remote providers
- Error handling for connection failures
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread
import time

import pytest

from mcp_hangar.domain.model import Provider
from mcp_hangar.domain.value_objects import ProviderMode, ProviderState


class MockMCPHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for MCP-over-HTTP testing."""

    # Class-level config for test customization
    auth_required = None  # None, "bearer", "api_key", "basic"
    expected_token = None
    should_fail = False
    response_delay = 0

    def log_message(self, format, *args):
        """Suppress logging in tests."""
        pass

    def do_POST(self):  # noqa: N802 - Required by BaseHTTPRequestHandler
        """Handle MCP JSON-RPC requests."""
        # Check authentication if required
        if self.auth_required:
            auth_header = self.headers.get("Authorization", "")
            api_key = self.headers.get("X-API-Key", "")

            if self.auth_required == "bearer":
                if not auth_header.startswith("Bearer ") or auth_header[7:] != self.expected_token:
                    self.send_error(401, "Unauthorized")
                    return
            elif self.auth_required == "api_key":
                if api_key != self.expected_token:
                    self.send_error(401, "Unauthorized")
                    return
            elif self.auth_required == "basic":
                if not auth_header.startswith("Basic "):
                    self.send_error(401, "Unauthorized")
                    return

        if self.should_fail:
            self.send_error(500, "Internal Server Error")
            return

        # Add artificial delay if configured
        if self.response_delay > 0:
            time.sleep(self.response_delay)

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        method = request.get("method", "")
        request_id = request.get("id")

        # Handle MCP methods
        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "mock-mcp-server", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo back the input",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        }
                    ]
                },
            }
        elif method == "tools/call":
            tool_name = request.get("params", {}).get("name")
            args = request.get("params", {}).get("arguments", {})

            if tool_name == "echo":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": args.get("message", "")}]},
                }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        # Send response
        response_body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response_body))
        self.end_headers()
        self.wfile.write(response_body)


@pytest.fixture
def mock_mcp_server():
    """Start a mock MCP HTTP server for testing."""
    # Reset handler state
    MockMCPHandler.auth_required = None
    MockMCPHandler.expected_token = None
    MockMCPHandler.should_fail = False
    MockMCPHandler.response_delay = 0

    server = HTTPServer(("127.0.0.1", 0), MockMCPHandler)
    port = server.server_address[1]

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()


class TestRemoteProviderConfiguration:
    """Test Provider configuration with remote mode."""

    def test_provider_with_remote_mode(self):
        """Should create provider with remote mode."""
        provider = Provider(
            provider_id="remote-test",
            mode="remote",
            endpoint="https://mcp.example.com/api",
        )
        assert provider.mode == ProviderMode.REMOTE
        assert provider.state == ProviderState.COLD

    def test_provider_with_auth_config(self):
        """Should accept authentication configuration."""
        provider = Provider(
            provider_id="remote-auth",
            mode="remote",
            endpoint="https://mcp.example.com/api",
            auth={
                "type": "bearer",
                "bearer_token": "secret-token",
            },
        )
        assert provider._auth_config is not None
        assert provider._auth_config["type"] == "bearer"

    def test_provider_with_tls_config(self):
        """Should accept TLS configuration."""
        provider = Provider(
            provider_id="remote-tls",
            mode="remote",
            endpoint="https://mcp.example.com/api",
            tls={
                "verify_ssl": False,
                "ca_cert_path": "/path/to/ca.pem",
            },
        )
        assert provider._tls_config is not None
        assert provider._tls_config["verify_ssl"] is False

    def test_provider_with_http_config(self):
        """Should accept HTTP transport configuration."""
        provider = Provider(
            provider_id="remote-http",
            mode="remote",
            endpoint="https://mcp.example.com/api",
            http={
                "connect_timeout": 5.0,
                "read_timeout": 60.0,
                "max_retries": 5,
            },
        )
        assert provider._http_config is not None
        assert provider._http_config["connect_timeout"] == 5.0


class TestRemoteProviderConnection:
    """Test actual connections to remote MCP providers."""

    def test_connect_to_mock_server(self, mock_mcp_server):
        """Should connect and initialize with mock MCP server."""
        provider = Provider(
            provider_id="mock-remote",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
        )

        # Ensure provider is ready (this triggers connection)
        provider.ensure_ready()

        assert provider.state == ProviderState.READY
        assert provider.has_tools
        assert provider.tools.has("echo")

    def test_connect_with_bearer_auth(self, mock_mcp_server):
        """Should authenticate with bearer token."""
        MockMCPHandler.auth_required = "bearer"
        MockMCPHandler.expected_token = "test-bearer-token"

        provider = Provider(
            provider_id="bearer-auth-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
            auth={
                "type": "bearer",
                "bearer_token": "test-bearer-token",
            },
        )

        provider.ensure_ready()
        assert provider.state == ProviderState.READY

    def test_connect_with_api_key_auth(self, mock_mcp_server):
        """Should authenticate with API key."""
        MockMCPHandler.auth_required = "api_key"
        MockMCPHandler.expected_token = "my-api-key"

        provider = Provider(
            provider_id="api-key-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
            auth={
                "type": "api_key",
                "api_key": "my-api-key",
            },
        )

        provider.ensure_ready()
        assert provider.state == ProviderState.READY

    def test_auth_failure_transitions_to_dead(self, mock_mcp_server):
        """Should transition to DEAD on authentication failure."""
        MockMCPHandler.auth_required = "bearer"
        MockMCPHandler.expected_token = "correct-token"

        provider = Provider(
            provider_id="auth-fail-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
            auth={
                "type": "bearer",
                "bearer_token": "wrong-token",
            },
        )

        with pytest.raises(Exception):
            provider.ensure_ready()

        # Should be in DEAD or DEGRADED state after failure
        assert provider.state in (ProviderState.DEAD, ProviderState.DEGRADED)


class TestRemoteProviderToolInvocation:
    """Test tool invocation on remote providers."""

    def test_invoke_tool_on_remote(self, mock_mcp_server):
        """Should invoke tools on remote provider."""
        provider = Provider(
            provider_id="tool-invoke-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
        )

        provider.ensure_ready()

        result = provider.invoke_tool("echo", {"message": "Hello, World!"})

        assert result is not None
        assert "content" in result


class TestRemoteProviderHealthCheck:
    """Test health checks for remote providers."""

    def test_health_check_success(self, mock_mcp_server):
        """Should report healthy when remote is responsive."""
        provider = Provider(
            provider_id="health-check-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
        )

        provider.ensure_ready()

        # Health should be tracked
        assert provider.health.consecutive_failures == 0

    def test_health_degraded_on_failure(self, mock_mcp_server):
        """Should degrade health on remote failures."""
        MockMCPHandler.should_fail = True

        provider = Provider(
            provider_id="health-fail-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
            max_consecutive_failures=2,
        )

        # Multiple failures should degrade provider
        for _ in range(3):
            try:
                provider.ensure_ready()
            except Exception:
                pass

        # Should have accumulated failures
        assert provider.health.consecutive_failures > 0


class TestRemoteProviderTimeout:
    """Test timeout handling for remote providers."""

    def test_connection_timeout(self):
        """Should handle connection timeout gracefully."""
        provider = Provider(
            provider_id="timeout-test",
            mode="remote",
            endpoint="http://10.255.255.1:9999/mcp",  # Non-routable IP
            http={
                "connect_timeout": 1.0,
                "read_timeout": 1.0,
            },
        )

        with pytest.raises(Exception):
            provider.ensure_ready()

        assert provider.state in (ProviderState.DEAD, ProviderState.DEGRADED)

    def test_read_timeout(self, mock_mcp_server):
        """Should handle read timeout."""
        MockMCPHandler.response_delay = 5.0  # 5 second delay

        provider = Provider(
            provider_id="read-timeout-test",
            mode="remote",
            endpoint=f"{mock_mcp_server}/mcp",
            http={
                "connect_timeout": 1.0,
                "read_timeout": 0.5,  # Very short timeout
            },
        )

        with pytest.raises(Exception):
            provider.ensure_ready()


class TestRemoteProviderConnectionRefused:
    """Test handling of connection refused errors."""

    def test_connection_refused(self):
        """Should handle connection refused gracefully."""
        provider = Provider(
            provider_id="conn-refused-test",
            mode="remote",
            endpoint="http://127.0.0.1:59999/mcp",  # Closed port
        )

        with pytest.raises(Exception):
            provider.ensure_ready()

        assert provider.state in (ProviderState.DEAD, ProviderState.DEGRADED)
