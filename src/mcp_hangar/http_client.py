"""HTTP client for MCP-over-HTTP mcp_servers.

Thread-safe HTTP client with:
- SSE (Server-Sent Events) streaming support
- Configurable authentication (none, API key, bearer token, basic auth)
- Connection pooling and retry logic
- TLS/HTTPS support with custom CA certificates
- Request/response correlation
- Prometheus metrics instrumentation

Follows the same interface as StdioClient for consistency.
"""

import json
import ssl
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from queue import Queue
from typing import Any, cast

import httpx

from . import metrics as prometheus_metrics
from .domain.exceptions import ClientError
from .domain.security.ssrf import SsrfBlocked, resolve_validated_addresses
from .domain.value_objects.provenance import Provenance
from .logging_config import get_logger
from .context import get_identity_context
from .observability.tracing import (
    inject_trace_context,
    scrub_baggage_for_tenant,
    upstream_call_span,
)
from .protocol import SESSION_TERMINATED_CODE, SESSION_TERMINATED_REASON, inject_protocol_meta

logger = get_logger(__name__)


class AuthType(Enum):
    """Authentication type for HTTP mcp_servers."""

    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class AuthConfig:
    """Authentication configuration for HTTP mcp_servers.

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
            assert self.api_key is not None
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
        stateless_upstream: Declare the upstream as stateless (SEP-2567). When
            True, the deprecated transport ``Mcp-Session-Id`` handling is fully
            disabled: Hangar neither captures nor echoes the header. Leave False
            (the default) only for backward-compat with legacy, session-based
            upstreams (pre-2026-07-28); in that mode the header is still echoed,
            but ONLY after such an upstream established a session by returning
            one -- stateless upstreams remain session-free either way.
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
    # SEP-2567: sessions are removed from the transport. This guard lets an
    # upstream be declared stateless so the deprecated Mcp-Session-Id handling
    # below is never exercised. Default False preserves legacy connectivity.
    stateless_upstream: bool = False
    # Connect-time SSRF policy for this upstream, applied on every connection by
    # `_SsrfGuardedTransport` -- but only when `enforce_ssrf` is set. It closes
    # DNS rebinding: `validate_no_ssrf` runs once at registration, yet httpx
    # re-resolves the hostname itself at connect time with no re-check, so a name
    # that passed once can be re-pointed at an internal address and every later
    # call follows it. `enforce_ssrf` is turned on for exactly the population the
    # registration check guarded (endpoints created through the command handler,
    # i.e. the REST API and discovery), and left off for endpoints that were
    # never registration-checked -- a config-file `remote` server pointed at an
    # internal address on purpose, or a directly-constructed client -- so the
    # connect guard cannot newly refuse a private endpoint the operator intended.
    # When on, `provenance` + `runtime_addresses` carry the same inputs the
    # registration check used; their defaults mirror `validate_no_ssrf`'s
    # fail-safe (HUMAN, no runtime-scoped addresses).
    enforce_ssrf: bool = False
    provenance: Provenance = Provenance.HUMAN
    runtime_addresses: frozenset[str] | None = None


@dataclass
class PendingHttpRequest:
    """Tracks a pending HTTP request waiting for a response."""

    request_id: str
    result_queue: Queue
    started_at: float


class _SsrfGuardedTransport(httpx.HTTPTransport):
    """An httpx transport that re-checks SSRF policy at connect time and pins.

    `validate_no_ssrf` runs once, at registration, against the addresses the
    hostname resolved to then. httpx then re-resolves that hostname on its own
    at every connect, with no second check -- so an upstream registered under a
    name that resolved to a public address can be re-pointed at 169.254.169.254
    / 10.x / 127.0.0.1 (DNS rebinding), and every later tool call connects to
    the internal address. This transport closes that gap:

    - On EVERY request (never cached) it resolves the host again and runs the
      same domain policy the registration check used. A refused address raises
      `SsrfBlocked`, wrapped as `httpx.ConnectError` so `HttpClient.call`
      already handles it as a connection failure.
    - It then PINS: the request URL host is rewritten to a validated IP literal
      so httpcore connects there (and pools by that IP), while the `Host` header
      keeps the original authority and `sni_hostname` keeps the original name --
      so TLS SNI and certificate verification still validate against the NAME,
      not the IP. httpx auto-brackets an IPv6 literal in `copy_with(host=...)`.
    """

    def __init__(
        self,
        *args: Any,
        enforce_ssrf: bool,
        provenance: Provenance,
        runtime_addresses: frozenset[str] | None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._enforce_ssrf = enforce_ssrf
        self._provenance = provenance
        self._runtime_addresses = runtime_addresses

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # Only endpoints the registration check guarded are re-checked here;
        # a config-file or directly-built client keeps httpx's plain behaviour,
        # so an intentionally private endpoint is not newly refused at connect.
        if not self._enforce_ssrf:
            return super().handle_request(request)

        original_host = request.url.host
        port = request.url.port
        scheme = request.url.scheme
        authority = original_host if port is None else f"{original_host}:{port}"

        try:
            validated = resolve_validated_addresses(
                f"{scheme}://{authority}",
                provenance=self._provenance,
                runtime_addresses=self._runtime_addresses,
            )
        except SsrfBlocked as e:
            # Surface as a connection error: HttpClient.call already catches
            # httpx.ConnectError and reports connection_failed, and there is no
            # connection to make to a refused address.
            raise httpx.ConnectError(f"SSRF guard blocked connection: {e}", request=request) from e

        if validated:
            pinned_ip = validated[0]
            # Preserve vhost + TLS identity while connecting by IP. httpx keeps
            # the Host header it built from the original URL (it is not
            # re-derived from the rewritten URL), but re-assert it explicitly so
            # the vhost is correct even if a caller pre-mutated the request.
            original_host_header = request.headers.get("Host") or authority
            request.url = request.url.copy_with(host=pinned_ip)
            request.headers["Host"] = original_host_header
            # httpcore uses sni_hostname for BOTH the TLS SNI and the certificate
            # hostname check, so verification validates the NAME we resolved, not
            # the IP we connect to.
            request.extensions["sni_hostname"] = original_host

        return super().handle_request(request)


class HttpClient:
    """
    Thread-safe HTTP client for MCP-over-HTTP mcp_servers.

    Implements the same interface as StdioClient for consistency.
    Supports both standard request/response and SSE streaming patterns.
    """

    def __init__(
        self,
        endpoint: str,
        auth_config: AuthConfig | None = None,
        http_config: HttpClientConfig | None = None,
        mcp_server_id: str | None = None,
    ):
        """
        Initialize HTTP client for a remote MCP mcp_server.

        Args:
            endpoint: Base URL of the MCP mcp_server (e.g., https://mcp.example.com)
            auth_config: Authentication configuration
            http_config: HTTP client configuration
            mcp_server_id: Optional mcp_server ID for metrics labeling
        """
        self._endpoint = endpoint.rstrip("/")
        self._auth_config = auth_config or AuthConfig()
        self._http_config = http_config or HttpClientConfig()
        self._mcp_server_id = mcp_server_id
        #: Whether this connection accepts the 2026-07-28 `_meta` envelope.
        #: Starts True so the handshake itself and stateless (SEP-2575) upstreams
        #: carry it; `_perform_mcp_handshake` clears it the moment a legacy
        #: `initialize` succeeds, because from mcp 2.0.0 such a connection
        #: rejects the modern envelope on every later request (-32600).
        self.modern_envelope = True

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

        # DEPRECATED (SEP-2567): MCP Streamable HTTP transport session tracking.
        # Sessions are removed from the transport for stateless upstreams
        # (2026-07-28+). This state is retained ONLY for backward-compat with
        # legacy session-based upstreams: such a server returns Mcp-Session-Id on
        # initialize, and older deployments require it echoed on subsequent
        # requests. It stays None (and is never sent) for stateless upstreams or
        # when ``stateless_upstream`` is set. NOTE: this transport session id is
        # unrelated to the audit/correlation ``CallerIdentity.session_id`` used
        # by compliance exporters, which is untouched by SEP-2567.
        self._mcp_session_id: str | None = None

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

        # The TLS settings go on the TRANSPORT, and that is the whole point.
        #
        # `httpx.Client(verify=...)` only configures the transport httpx would
        # have built for itself. Passing `transport=` explicitly -- which this
        # client does, for retries -- replaces that transport with one
        # constructed here, and a transport built without `verify` uses the
        # default: verify against the system trust store. So the `verify`
        # argument below was dead, and every TLS setting an operator could
        # write was silently discarded.
        #
        # Measured against a self-signed upstream on 2.5.0-rc.3, all three
        # through the same httpx:
        #
        #     verify=False, no explicit transport            -> 200
        #     verify=False + transport without verify        -> ConnectError
        #     transport built with verify=False              -> 200
        #
        # It failed closed, which is why it went unnoticed: `verify_ssl: false`
        # simply did not work. `ca_cert_path` travels on the same argument and
        # was discarded the same way -- and that one has no safe reading, since
        # it is how a deployment trusts its own internal CA.
        # `_SsrfGuardedTransport`, not a plain `httpx.HTTPTransport`: it re-runs
        # the SSRF policy and pins to a validated IP on every connect, closing
        # the DNS-rebinding gap left by the registration-time-only check. The
        # `retries` and `verify` arguments must still reach the transport
        # unchanged -- see the long comment above on why TLS settings live here.
        transport = _SsrfGuardedTransport(
            # Zero, because `_post_with_retry` owns retrying now (#1163).
            # httpcore's loop retries connect failures only, on a hardcoded
            # backoff the operator cannot configure and a metric the application
            # cannot emit from; leaving it at `max_retries` as well would
            # multiply the two loops together.
            retries=0,
            verify=verify,
            enforce_ssrf=config.enforce_ssrf,
            provenance=config.provenance,
            runtime_addresses=config.runtime_addresses,
        )

        return httpx.Client(
            timeout=timeout,
            headers=headers,
            transport=transport,
            follow_redirects=False,  # SSRF prevention: redirects must be validated explicitly
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

    def _post_with_retry(
        self,
        url: str,
        request_body: dict[str, Any],
        headers: dict[str, str] | None,
        timeout: float | None,
        mcp_server_label: str,
    ) -> httpx.Response:
        """POST, retrying the transient failures the config says to retry.

        `max_retries`, `retry_backoff_factor` and `retry_status_codes` were
        declared, validated, parsed from `http:`, passed through the launcher
        and documented -- and the only retry that ran was httpcore's, which
        retries `ConnectError`/`ConnectTimeout` alone, with its own hardcoded
        backoff. A 502/503/504 from an upstream mid-rollout came straight back
        to the caller on the first attempt, `retry_backoff_factor` and
        `retry_status_codes` had no reader at all, and
        `mcp_hangar_http_retries_total` -- registered, on a shipped Grafana
        panel, in the docs -- could not be incremented from inside a retry loop
        the application cannot see (#1163).

        The transport is now built with `retries=0` so connect retries happen
        here too, once, under the configured backoff rather than two loops deep.

        Args:
            url: The upstream endpoint.
            request_body: JSON-RPC request payload.
            headers: Per-request headers, or None.
            timeout: Per-request timeout.
            mcp_server_label: Metric label for this upstream.

        Returns:
            The last response received -- a retried status is returned as-is
            once the attempts are spent, so the caller's existing error
            handling is unchanged.
        """
        attempts = max(1, self._http_config.max_retries)
        retry_statuses = set(self._http_config.retry_status_codes)
        backoff = self._http_config.retry_backoff_factor

        for attempt in range(attempts):
            reason: str
            try:
                response = self._client.post(url, json=request_body, headers=headers, timeout=timeout)
                if response.status_code not in retry_statuses or attempt == attempts - 1:
                    return response
                reason = str(response.status_code)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt == attempts - 1:
                    raise
                reason = "connection_error"
                logger.debug("http_client_connect_failed", mcp_server=mcp_server_label, error=str(exc))

            prometheus_metrics.HTTP_RETRIES_TOTAL.inc(mcp_server=mcp_server_label, retry_reason=reason)
            # Exponential on the factor, the shape `retry_backoff_factor`
            # names; a factor of 0 disables waiting without disabling retries.
            time.sleep(backoff * (2**attempt))

        # Unreachable: the loop returns or raises on its last attempt.
        raise ClientError("retry_loop_exhausted")

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

        # Resolve the tenant this outbound request is attributed to, so we can
        # strip any cross-tenant / untrusted W3C baggage before it leaves.
        _identity = get_identity_context()
        _tenant_id = _identity.caller.tenant_id if _identity and _identity.caller else None

        params = inject_protocol_meta(params, modern_envelope=self.modern_envelope)
        # SEP-414: carry W3C trace context in params._meta (not only HTTP headers),
        # so it survives across MCP hops regardless of transport.
        inject_trace_context(params["_meta"])
        # Fail-safe cross-tenant scrub: drop untrusted/cross-tenant baggage on outbound.
        scrub_baggage_for_tenant(params["_meta"], _tenant_id)
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

        # Get mcp_server label for metrics (use mcp_server_id or extract from host)
        mcp_server_label = self._mcp_server_id or self._host

        try:
            # CLIENT span at the upstream boundary (OTel GenAI/MCP semconv),
            # opened before injection so the traceparent written into the
            # request headers parents the upstream's span to this one.
            with upstream_call_span(method, params):
                # Build per-request headers: W3C TraceContext (+ deprecated session id).
                extra_headers: dict[str, str] = {}
                inject_trace_context(extra_headers)
                # Fail-safe cross-tenant scrub: drop untrusted/cross-tenant baggage on outbound.
                scrub_baggage_for_tenant(extra_headers, _tenant_id)
                # DEPRECATED (SEP-2567): only echo the transport Mcp-Session-Id for a
                # legacy session-based upstream that established one. Declaring the
                # upstream stateless keeps _mcp_session_id None, so this is skipped.
                if not self._http_config.stateless_upstream and self._mcp_session_id:
                    extra_headers["Mcp-Session-Id"] = self._mcp_session_id

                prometheus_metrics.record_message_sent(mcp_server_label, method, len(json.dumps(request_body).encode()))
                response = self._post_with_retry(
                    url,
                    request_body,
                    extra_headers if extra_headers else None,
                    timeout,
                    mcp_server_label,
                )

            duration_s = time.time() - start_time
            duration_ms = duration_s * 1000
            status_code = str(response.status_code)

            # DEPRECATED (SEP-2567): capture the transport session id ONLY for
            # backward-compat with legacy session-based upstreams. Stateless
            # upstreams do not return this header, and declaring the upstream
            # stateless suppresses capture entirely so no session is ever tracked.
            if not self._http_config.stateless_upstream:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._mcp_session_id = session_id

            # Record HTTP request metrics
            prometheus_metrics.HTTP_REQUESTS_TOTAL.inc(
                mcp_server=mcp_server_label, method=method, status_code=status_code
            )
            prometheus_metrics.HTTP_REQUEST_DURATION_SECONDS.observe(
                duration_s, mcp_server=mcp_server_label, method=method
            )

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

            if response.status_code == 404 and self._mcp_session_id is not None:
                # The session this client holds no longer exists upstream --
                # typically because the upstream process restarted. Streamable
                # HTTP answers a request carrying an unknown Mcp-Session-Id with
                # 404, and the resolution is to establish a new session rather
                # than keep presenting the dead one.
                #
                # Nothing did that. The id was captured once and never cleared,
                # 404 is not in `retry_status_codes`, and the caller saw an
                # opaque "HTTP error: 404". So every call after an upstream
                # restart failed, forever, while /health/ready still reported the
                # gateway healthy -- it recovered only when the gateway itself
                # was restarted (#651).
                #
                # Dropping the id here is half the fix; the other half is the
                # re-handshake, which lives in the domain layer because only it
                # knows how to `initialize`.
                dead_session = self._mcp_session_id
                self._mcp_session_id = None
                logger.warning(
                    "http_client_session_terminated",
                    request_id=request_id,
                    mcp_server=mcp_server_label,
                    method=method,
                    session_id=dead_session,
                )
                prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type="session_terminated")
                return {
                    "error": {
                        "code": SESSION_TERMINATED_CODE,
                        "message": "Session terminated",
                        "data": {"reason": SESSION_TERMINATED_REASON},
                    }
                }

            if response.status_code >= 400:
                prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type=f"http_{status_code}")
                return {
                    "error": {
                        "code": -32000,
                        "message": f"HTTP error: {response.status_code}",
                        "data": response.text[:500],
                    }
                }

            try:
                result = response.json()
                if isinstance(result, dict):
                    prometheus_metrics.record_message_received(
                        mcp_server_label,
                        prometheus_metrics.classify_jsonrpc_message(result),
                        len(response.content),
                    )
                return cast(dict[str, Any], result)
            except json.JSONDecodeError as e:
                prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type="json_decode_error")
                return {
                    "error": {
                        "code": -32700,
                        "message": f"Invalid JSON response: {e}",
                    }
                }

        except httpx.TimeoutException as e:
            duration_s = time.time() - start_time
            duration_ms = duration_s * 1000
            logger.error(
                "http_client_timeout",
                request_id=request_id,
                error=str(e),
                duration_ms=duration_ms,
            )
            # Record timeout error
            prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type="timeout")
            prometheus_metrics.HTTP_REQUEST_DURATION_SECONDS.observe(
                duration_s, mcp_server=mcp_server_label, method=method
            )
            raise TimeoutError(f"timeout: {method} after {timeout}s") from e

        except httpx.ConnectError as e:
            duration_s = time.time() - start_time
            duration_ms = duration_s * 1000
            logger.error(
                "http_client_connection_error",
                request_id=request_id,
                error=str(e),
                duration_ms=duration_ms,
            )
            # Record connection error
            prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type="connection_refused")
            raise ClientError(f"connection_failed: {e}") from e

        except Exception as e:  # noqa: BLE001 -- infra-boundary: unexpected HTTP errors wrapped as ClientError
            duration_s = time.time() - start_time
            duration_ms = duration_s * 1000
            logger.error(
                "http_client_request_failed",
                request_id=request_id,
                error=str(e),
                duration_ms=duration_ms,
            )
            # Record generic error
            prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type="request_failed")
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
                                return cast(dict[str, Any], msg)
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
        except Exception as e:  # noqa: BLE001 -- infra-boundary: SSE errors wrapped as JSON-RPC error response
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
                return cast(dict[str, Any], msg)

            # Log other messages (notifications, etc.)
            logger.debug("http_client_sse_notification", message_id=msg_id, expected_id=request_id)
            return None

        except json.JSONDecodeError:
            logger.warning("http_client_sse_invalid_json", data=data_line[:100])
            return None

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification: no id, no response to correlate.

        The counterpart to :meth:`call`, and until #881 there was no such thing
        on either transport -- both mint a request id unconditionally and then
        block on the matching response, so a notification could not be expressed
        at all. That is why the MCP lifecycle was never finished (see
        ``McpServer._perform_mcp_handshake``).

        Streamable HTTP answers a notification with ``202 Accepted`` and an
        empty body. Any 2xx is accepted; anything else is logged and raised,
        because a notification that did not arrive is worth knowing about even
        though there is no result to return.

        Carries the same protocol ``_meta``, trace context and session header a
        request does, so the era gate applies here too: a legacy connection must
        not be sent the 2026-07-28 envelope, and ``modern_envelope`` says so.

        Args:
            method: JSON-RPC method name, e.g. ``notifications/initialized``.
            params: Method parameters. Empty when omitted.

        Raises:
            ClientError: If the client is closed, the request fails, or the
                upstream answers non-2xx.
        """
        if self._closed:
            raise ClientError("client_closed")

        identity = get_identity_context()
        tenant_id = identity.caller.tenant_id if identity and identity.caller else None

        sent_params = inject_protocol_meta(params or {}, modern_envelope=self.modern_envelope)
        inject_trace_context(sent_params["_meta"])
        scrub_baggage_for_tenant(sent_params["_meta"], tenant_id)
        body = {"jsonrpc": "2.0", "method": method, "params": sent_params}

        mcp_server_label = self._mcp_server_id or self._host
        try:
            with upstream_call_span(method, sent_params):
                headers: dict[str, str] = {}
                inject_trace_context(headers)
                scrub_baggage_for_tenant(headers, tenant_id)
                if not self._http_config.stateless_upstream and self._mcp_session_id:
                    headers["Mcp-Session-Id"] = self._mcp_session_id

                prometheus_metrics.record_message_sent(mcp_server_label, method, len(json.dumps(body).encode()))
                response = self._client.post(
                    self._endpoint,
                    json=body,
                    headers=headers or None,
                    timeout=self._http_config.read_timeout,
                )
        except Exception as e:  # noqa: BLE001 -- infra-boundary: transport failure wrapped as ClientError
            prometheus_metrics.HTTP_ERRORS_TOTAL.inc(mcp_server=mcp_server_label, error_type="notify_failed")
            logger.error("http_client_notify_failed", method=method, error=str(e))
            raise ClientError(f"notify_failed: {e}") from e

        if response.status_code >= 300:
            prometheus_metrics.HTTP_ERRORS_TOTAL.inc(
                mcp_server=mcp_server_label, error_type=f"notify_http_{response.status_code}"
            )
            raise ClientError(f"notify_rejected: HTTP {response.status_code}")

        logger.debug("http_client_notification_sent", method=method, status=response.status_code)

    def start_notification_stream(self, on_message: Callable[[dict[str, Any]], None]) -> None:
        """Open and hold the standing ``GET`` stream for server-initiated messages (#882).

        Streamable HTTP gives an upstream two ways to reach its client: the SSE
        body of a POST response, and a standing ``GET`` stream. Until this
        existed we opened neither for anything but request/response, so every
        unprompted server message -- ``notifications/progress``,
        ``notifications/tools/list_changed``, the upstream's own log lines --
        was silently unreachable.

        The stream is read on a daemon thread and reconnects with backoff while
        the client is open. An upstream that answers the ``GET`` with 404/405
        does not offer the channel; that is a normal answer, logged once, and
        the thread exits rather than hammering it.

        Args:
            on_message: Called with each decoded JSON-RPC message from the
                stream. Exceptions it raises are logged, not propagated -- a bad
                handler must not kill the channel.
        """
        if self._sse_thread is not None or self._closed:
            return
        self._sse_running = True
        self._sse_thread = threading.Thread(
            target=self._notification_stream_loop,
            args=(on_message,),
            name=f"mcp-get-stream-{self._mcp_server_id or self._host}",
            daemon=True,
        )
        self._sse_thread.start()

    def _notification_stream_loop(self, on_message: Callable[[dict[str, Any]], None]) -> None:
        mcp_server_label = self._mcp_server_id or self._host
        backoff = 1.0
        while self._sse_running and not self._closed:
            try:
                headers = {"Accept": "text/event-stream"}
                if not self._http_config.stateless_upstream and self._mcp_session_id:
                    headers["Mcp-Session-Id"] = self._mcp_session_id
                # read=None: this stream is MEANT to sit idle between events.
                timeout = httpx.Timeout(connect=self._http_config.connect_timeout, read=None, write=None, pool=None)
                with self._client.stream("GET", self._endpoint, headers=headers, timeout=timeout) as response:
                    if response.status_code in (404, 405):
                        logger.info(
                            "http_client_get_stream_unsupported",
                            mcp_server=mcp_server_label,
                            status=response.status_code,
                        )
                        return
                    if response.status_code >= 300:
                        raise ClientError(f"get_stream_rejected: HTTP {response.status_code}")
                    logger.info("http_client_get_stream_open", mcp_server=mcp_server_label)
                    backoff = 1.0
                    buffer = ""
                    for chunk in response.iter_text():
                        if not self._sse_running or self._closed:
                            return
                        buffer += chunk
                        while "\n\n" in buffer:
                            event_data, buffer = buffer.split("\n\n", 1)
                            self._dispatch_stream_event(event_data, on_message, mcp_server_label)
            except Exception as e:  # noqa: BLE001 -- the channel outlives any single transport failure
                if not self._sse_running or self._closed:
                    return
                logger.warning(
                    "http_client_get_stream_error",
                    mcp_server=mcp_server_label,
                    error=str(e),
                    retry_in_s=backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _dispatch_stream_event(
        self,
        event_data: str,
        on_message: Callable[[dict[str, Any]], None],
        mcp_server_label: str,
    ) -> None:
        data_lines = [line.strip()[5:].strip() for line in event_data.split("\n") if line.strip().startswith("data:")]
        data = "\n".join(line for line in data_lines if line)
        if not data:
            return
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("http_client_get_stream_invalid_json", mcp_server=mcp_server_label, data=data[:100])
            return
        if not isinstance(msg, dict):
            return
        prometheus_metrics.record_message_received(
            mcp_server_label, prometheus_metrics.classify_jsonrpc_message(msg), len(data)
        )
        try:
            on_message(msg)
        except Exception as e:  # noqa: BLE001 -- a bad handler must not kill the channel
            logger.error(
                "http_client_stream_handler_failed",
                mcp_server=mcp_server_label,
                method=msg.get("method"),
                error=str(e),
            )

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

        # SEP-2567 leftover duty (#882): a legacy session-based upstream gave us
        # a session; abandoning it leaves server-side resources held until its
        # own timer expires, and a restarting gateway accumulates them. A modern
        # stateless upstream has no session, so this is skipped entirely. A
        # teardown must not fail a shutdown -- log and move on.
        if self._mcp_session_id and not self._http_config.stateless_upstream:
            try:
                self._client.delete(
                    self._endpoint,
                    headers={"Mcp-Session-Id": self._mcp_session_id},
                    timeout=5.0,
                )
                logger.debug("http_client_session_deleted", session_id=self._mcp_session_id)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: teardown must not fail a shutdown
                logger.warning("http_client_session_delete_failed", error=str(e))

        # Close httpx client
        try:
            self._client.close()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: close cleanup must not propagate
            logger.debug("http_client_close_error", error=str(e))

        # Clean up pending requests
        with self._pending_lock:
            for pending in self._pending.values():
                pending.result_queue.put({"error": {"code": -1, "message": "client_closed"}})
            self._pending.clear()

        logger.info("http_client_closed", endpoint=self._endpoint)

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @property
    def endpoint(self) -> str:
        """Get the endpoint URL."""
        return self._endpoint
