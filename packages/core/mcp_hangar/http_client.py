"""HTTP client for MCP-over-HTTP providers.

Thread-safe HTTP client with:
- SSE (Server-Sent Events) streaming support
- Configurable authentication (none, API key, bearer token, basic auth)
- Connection pooling and retry logic
- TLS/HTTPS support with custom CA certificates
- Request/response correlation

Follows the same interface as StdioClient for consistency.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
from queue import Queue
import ssl
import threading
import time
from typing import Any
import uuid

import httpx

from .domain.exceptions import ClientError
from .logging_config import get_logger

logger = get_logger(__name__)


class AuthType(Enum):
    """Authentication type for HTTP providers."""

    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class AuthConfig:
    """Authentication configuration for HTTP providers.

    Immutable value object containing auth credentials.
    Secrets should be passed via environment variable interpolation.

    Attributes:
        auth_type: Type of authentication to use.
        api_key: API key for api_key auth (header value).
        api_key_header: Header name for API key (default: X-API-Key).
        bearer_token: Bearer token for bearer auth.
        basic_username: Username for basic auth.
        basic_password: Password for basic auth.
    """

    auth_type: AuthType = AuthType.NONE
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
    bearer_token: str | None = None
    basic_username: str | None = None
    basic_password: str | None = None

    def __post_init__(self) -> None:
        """Validate auth configuration."""
        if self.auth_type == AuthType.API_KEY and not self.api_key:
            raise ValueError("api_key is required for api_key auth type")
        if self.auth_type == AuthType.BEARER and not self.bearer_token:
            raise ValueError("bearer_token is required for bearer auth type")
        if self.auth_type == AuthType.BASIC:
            if not self.basic_username or not self.basic_password:
                raise ValueError("basic_username and basic_password are required for basic auth type")

    def get_headers(self) -> dict[str, str]:
        """Get authentication headers for HTTP requests.

        Returns:
            Dictionary of headers to add to requests.
        """
        if self.auth_type == AuthType.NONE:
            return {}

        if self.auth_type == AuthType.API_KEY:
            return {self.api_key_header: self.api_key}

        if self.auth_type == AuthType.BEARER:
            return {"Authorization": f"Bearer {self.bearer_token}"}

        if self.auth_type == AuthType.BASIC:
            import base64

            credentials = f"{self.basic_username}:{self.basic_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}

        return {}


@dataclass
class HttpClientConfig:
    """Configuration for HTTP client.

    Attributes:
        connect_timeout: Connection timeout in seconds.
        read_timeout: Read timeout in seconds.
        total_timeout: Total request timeout in seconds.
        max_retries: Maximum number of retry attempts.
        retry_backoff_factor: Exponential backoff factor for retries.
        retry_status_codes: HTTP status codes that trigger retries.
        verify_ssl: Whether to verify SSL certificates.
        ca_cert_path: Path to custom CA certificate file.
        keep_alive: Whether to use HTTP keep-alive.
        pool_connections: Number of connection pool connections.
        pool_maxsize: Maximum pool size.
        extra_headers: Additional headers to include in all requests.
    """

    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    total_timeout: float = 60.0
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    retry_status_codes: tuple[int, ...] = (502, 503, 504)
    verify_ssl: bool = True
    ca_cert_path: str | None = None
    keep_alive: bool = True
    pool_connections: int = 10
    pool_maxsize: int = 10
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class PendingHttpRequest:
    """Tracks a pending HTTP request waiting for a response."""

    request_id: str
    result_queue: Queue
    started_at: float


class HttpClient:
    """
    Thread-safe HTTP client for MCP-over-HTTP providers.

    Implements the same interface as StdioClient for consistency.
    Supports both standard request/response and SSE streaming patterns.
    """

    def __init__(
        self,
        endpoint: str,
        auth_config: AuthConfig | None = None,
        http_config: HttpClientConfig | None = None,
    ):
        """
        Initialize HTTP client for a remote MCP provider.

        Args:
            endpoint: Base URL of the MCP provider (e.g., https://mcp.example.com)
            auth_config: Authentication configuration
            http_config: HTTP client configuration
        """
        self._endpoint = endpoint.rstrip("/")
        self._auth_config = auth_config or AuthConfig()
        self._http_config = http_config or HttpClientConfig()

        # Parse endpoint URL
        self._scheme, self._host, self._port, self._base_path = self._parse_endpoint(endpoint)

        # Create httpx client with retry transport
        self._client = self._create_client()

        # Request tracking for SSE correlation
        self._pending: dict[str, PendingHttpRequest] = {}
        self._pending_lock = threading.Lock()

        # SSE reader thread (lazy-started)
        self._sse_thread: threading.Thread | None = None
        self._sse_running = False

        # Client state
        self._closed = False

        logger.info(
            "http_client_initialized",
            endpoint=self._endpoint,
            auth_type=self._auth_config.auth_type.value,
        )

    def _parse_endpoint(self, endpoint: str) -> tuple[str, str, int, str]:
        """Parse endpoint URL into components."""
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        scheme = parsed.scheme or "https"
        host = parsed.hostname or "localhost"

        if parsed.port:
            port = parsed.port
        else:
            port = 443 if scheme == "https" else 80

        base_path = parsed.path.rstrip("/") if parsed.path else ""

        return scheme, host, port, base_path

    def _create_client(self) -> httpx.Client:
        """Create httpx client with appropriate configuration."""
        config = self._http_config

        # SSL context for HTTPS
        verify: bool | str | ssl.SSLContext = config.verify_ssl
        if config.ca_cert_path:
            verify = config.ca_cert_path

        # Timeout configuration
        timeout = httpx.Timeout(
            connect=config.connect_timeout,
            read=config.read_timeout,
            write=config.connect_timeout,
            pool=config.connect_timeout,
        )

        # Build base headers
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self._auth_config.get_headers())
        headers.update(config.extra_headers)

        # Create transport with retry logic
        transport = httpx.HTTPTransport(
            retries=config.max_retries,
        )

        return httpx.Client(
            timeout=timeout,
            headers=headers,
            verify=verify,
            transport=transport,
            follow_redirects=True,
        )

    def _build_headers(self) -> dict[str, str]:
        """Build request headers including auth and custom headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Add auth headers
        headers.update(self._auth_config.get_headers())

        # Add custom headers
        headers.update(self._http_config.extra_headers)

        return headers

    def call(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Synchronous RPC call over HTTP.

        Args:
            method: JSON-RPC method name
            params: Method parameters
            timeout: Request timeout in seconds. If None, uses configured read_timeout.

        Returns:
            Response dictionary with either 'result' or 'error' key

        Raises:
            ClientError: If the client is closed or request fails
            TimeoutError: If the request times out
        """
        # Use configured timeout if not explicitly specified
        if timeout is None:
            timeout = self._http_config.read_timeout
        if self._closed:
            raise ClientError("client_closed")

        request_id = str(uuid.uuid4())

        request_body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        # Use endpoint directly - it should already include the full MCP path
        url = self._endpoint

        logger.debug(
            "http_client_sending_request",
            method=method,
            endpoint=self._endpoint,
            request_id=request_id,
        )

        start_time = time.time()

        try:
            response = self._client.post(
                url,
                json=request_body,
                timeout=timeout,
            )

            duration_ms = (time.time() - start_time) * 1000

            # Check for SSE response (streaming)
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                # For SSE, response body is already read by httpx
                # Parse it directly as SSE format
                return self._parse_sse_body(response.text, request_id)

            logger.debug(
                "http_client_response_received",
                request_id=request_id,
                status=response.status_code,
                duration_ms=duration_ms,
            )

            if response.status_code >= 400:
                return {
                    "error": {
                        "code": -32000,
                        "message": f"HTTP error: {response.status_code}",
                        "data": response.text[:500],
                    }
                }

            try:
                result = response.json()
                return result
            except json.JSONDecodeError as e:
                return {
                    "error": {
                        "code": -32700,
                        "message": f"Invalid JSON response: {e}",
                    }
                }

        except httpx.TimeoutException as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "http_client_timeout",
                request_id=request_id,
                error=str(e),
                duration_ms=duration_ms,
            )
            raise TimeoutError(f"timeout: {method} after {timeout}s") from e

        except httpx.ConnectError as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "http_client_connection_error",
                request_id=request_id,
                error=str(e),
                duration_ms=duration_ms,
            )
            raise ClientError(f"connection_failed: {e}") from e

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "http_client_request_failed",
                request_id=request_id,
                error=str(e),
                duration_ms=duration_ms,
            )
            raise ClientError(f"request_failed: {e}") from e

    def _parse_sse_body(self, body: str, request_id: str) -> dict[str, Any]:
        """
        Parse SSE response body that was already fully read.

        Args:
            body: Full SSE response body text
            request_id: Our request ID to match

        Returns:
            JSON-RPC response dictionary
        """
        logger.debug("http_client_parsing_sse_body", request_id=request_id, body_length=len(body))

        # Split by double newline to get events
        events = body.split("\n\n")

        for event_data in events:
            if not event_data.strip():
                continue

            result = self._parse_sse_event(event_data, request_id)
            if result is not None:
                logger.debug("http_client_sse_response_found", request_id=request_id)
                return result

        # No matching response found - this might happen if server doesn't echo our ID
        # Try to find any valid JSON-RPC response
        for event_data in events:
            if not event_data.strip():
                continue
            for line in event_data.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    data_content = line[5:].strip()
                    if data_content:
                        try:
                            msg = json.loads(data_content)
                            if "result" in msg or "error" in msg:
                                logger.debug(
                                    "http_client_sse_found_response_without_id_match",
                                    msg_id=msg.get("id"),
                                    expected_id=request_id,
                                )
                                return msg
                        except json.JSONDecodeError:
                            pass

        return {
            "error": {
                "code": -32000,
                "message": "SSE response did not contain valid JSON-RPC response",
            }
        }

    def _handle_sse_response(
        self,
        response: httpx.Response,
        request_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        """
        Handle SSE (Server-Sent Events) streaming response.

        Reads events from the SSE stream until we get the response
        for our request ID.

        Args:
            response: HTTP response with SSE stream
            request_id: Our request ID to wait for
            timeout: Remaining timeout

        Returns:
            JSON-RPC response dictionary
        """
        start_time = time.time()
        buffer = ""

        logger.debug("http_client_sse_stream_started", request_id=request_id)

        try:
            # Read SSE events
            for chunk in response.iter_text():
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"SSE timeout after {timeout}s")

                if not chunk:
                    continue

                buffer += chunk

                # Process complete events
                while "\n\n" in buffer:
                    event_data, buffer = buffer.split("\n\n", 1)
                    result = self._parse_sse_event(event_data, request_id)
                    if result is not None:
                        return result

            # Stream ended without response
            return {
                "error": {
                    "code": -32000,
                    "message": "SSE stream ended without response",
                }
            }

        except TimeoutError:
            raise
        except Exception as e:
            logger.error("http_client_sse_error", request_id=request_id, error=str(e))
            return {
                "error": {
                    "code": -32000,
                    "message": f"SSE error: {e}",
                }
            }

    def _parse_sse_event(self, event_data: str, request_id: str) -> dict[str, Any] | None:
        """
        Parse a single SSE event.

        Args:
            event_data: Raw SSE event data
            request_id: Our request ID to match

        Returns:
            JSON-RPC response if this event matches our request, None otherwise
        """
        data_line = None

        for line in event_data.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                # Handle both "data: {...}" and "data:{...}"
                data_content = line[5:].strip()
                if data_content:
                    data_line = data_content

        if not data_line:
            return None

        try:
            msg = json.loads(data_line)

            # Check if this is our response - compare string representations
            msg_id = msg.get("id")
            if msg_id is not None and str(msg_id) == str(request_id):
                return msg

            # Log other messages (notifications, etc.)
            logger.debug("http_client_sse_notification", message_id=msg_id, expected_id=request_id)
            return None

        except json.JSONDecodeError:
            logger.warning("http_client_sse_invalid_json", data=data_line[:100])
            return None

    def is_alive(self) -> bool:
        """Check if the HTTP client connection is alive.

        For HTTP, we consider the client alive if:
        - Not explicitly closed
        """
        if self._closed:
            return False

        return True

    def close(self) -> None:
        """
        Close the HTTP client and release resources.
        Safe to call multiple times.
        """
        if self._closed:
            return

        self._closed = True

        # Stop SSE reader if running
        self._sse_running = False

        # Close httpx client
        try:
            self._client.close()
        except Exception as e:
            logger.debug("http_client_close_error", error=str(e))

        # Clean up pending requests
        with self._pending_lock:
            for pending in self._pending.values():
                pending.result_queue.put({"error": {"code": -1, "message": "client_closed"}})
            self._pending.clear()

        logger.info("http_client_closed", endpoint=self._endpoint)

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.close()
        return False

    @property
    def endpoint(self) -> str:
        """Get the endpoint URL."""
        return self._endpoint
