"""HTTP mcp_server launcher implementation."""

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from mcp_hangar.logging_config import get_logger
from mcp_hangar.domain.exceptions import McpServerStartError, ValidationError
from mcp_hangar.domain.security.input_validator import InputValidator
from mcp_hangar.domain.value_objects.provenance import Provenance
from .base import McpServerLauncher

logger = get_logger(__name__)


class HttpLauncher(McpServerLauncher):
    """
    Launcher for remote HTTP-based MCP mcp_servers.

    Connects to MCP mcp_servers exposed via HTTP/HTTPS endpoints.
    Supports:
    - Multiple authentication schemes (none, API key, bearer token, basic)
    - SSE (Server-Sent Events) streaming
    - TLS with custom CA certificates
    - Connection pooling and retry logic

    Note: This launcher does not start a process - it creates a client
    that connects to an already-running remote mcp_server.
    """

    def __init__(
        self,
        verify_ssl: bool = True,
        ca_cert_path: str | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
    ):
        """
        Initialize HTTP launcher with default configuration.

        Args:
            verify_ssl: Whether to verify SSL certificates.
            ca_cert_path: Path to custom CA certificate file.
            connect_timeout: Default connection timeout in seconds.
            read_timeout: Default read timeout in seconds.
            max_retries: Default maximum retry attempts.
        """
        self._verify_ssl = verify_ssl
        self._ca_cert_path = ca_cert_path
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_retries = max_retries

        self._validator = InputValidator()

    def _validate_endpoint(self, endpoint: str) -> None:
        """
        Validate HTTP endpoint URL.

        Raises:
            ValidationError: If endpoint is invalid.
        """
        if not endpoint:
            raise ValidationError(message="Endpoint is required", field="endpoint")

        from urllib.parse import urlparse

        parsed = urlparse(endpoint)

        if not parsed.scheme:
            raise ValidationError(
                message="Endpoint must include scheme (http or https)",
                field="endpoint",
                value=endpoint,
            )

        if parsed.scheme not in ("http", "https"):
            raise ValidationError(
                message=f"Unsupported endpoint scheme: {parsed.scheme}. Use http or https.",
                field="endpoint",
                value=endpoint,
            )

        if not parsed.netloc:
            raise ValidationError(
                message="Endpoint must include host",
                field="endpoint",
                value=endpoint,
            )

    def launch(
        self,
        endpoint: str,
        auth_config: Mapping[str, object] | None = None,
        tls_config: Mapping[str, object] | None = None,
        http_config: Mapping[str, object] | None = None,
        provenance: Provenance = Provenance.HUMAN,
        runtime_addresses: frozenset[str] | None = None,
        enforce_ssrf: bool = False,
    ):
        """
        Create an HTTP client for a remote MCP mcp_server.

        Args:
            endpoint: HTTP/HTTPS URL of the MCP mcp_server.
            auth_config: Authentication configuration dict.
            tls_config: TLS configuration dict.
            http_config: HTTP transport configuration dict.

        Returns:
            HttpClient connected to the remote mcp_server.

        Raises:
            ValidationError: If inputs fail validation.
            McpServerStartError: If connection cannot be established.
        """
        # Validate endpoint
        self._validate_endpoint(endpoint)

        # Import here to avoid circular imports
        from mcp_hangar.http_client import AuthConfig, AuthType, HttpClient, HttpClientConfig

        # Build auth config
        auth = AuthConfig()
        if auth_config:
            auth_type_str = cast(str, auth_config.get("type", "none"))
            try:
                auth_type = AuthType(auth_type_str)
            except ValueError as e:
                raise ValidationError(
                    message=f"Invalid auth type: {auth_type_str}. Use: none, api_key, bearer, basic.",
                    field="auth.type",
                    value=auth_type_str,
                ) from e

            auth = AuthConfig(
                auth_type=auth_type,
                api_key=cast(str | None, auth_config.get("api_key")),
                api_key_header=cast(str, auth_config.get("api_key_header", "X-API-Key")),
                bearer_token=cast(str | None, auth_config.get("bearer_token")),
                basic_username=cast(str | None, auth_config.get("username")),
                basic_password=cast(str | None, auth_config.get("password")),
            )

        # Build HTTP client config
        http_cfg = HttpClientConfig(
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            max_retries=self._max_retries,
            verify_ssl=self._verify_ssl,
            ca_cert_path=self._ca_cert_path,
        )

        if tls_config:
            http_cfg = HttpClientConfig(
                connect_timeout=http_cfg.connect_timeout,
                read_timeout=http_cfg.read_timeout,
                max_retries=http_cfg.max_retries,
                verify_ssl=cast(bool, tls_config.get("verify_ssl", True)),
                ca_cert_path=cast(str | None, tls_config.get("ca_cert_path")),
            )

        if http_config:
            http_cfg = HttpClientConfig(
                connect_timeout=cast(float, http_config.get("connect_timeout", http_cfg.connect_timeout)),
                read_timeout=cast(float, http_config.get("read_timeout", http_cfg.read_timeout)),
                max_retries=cast(int, http_config.get("max_retries", http_cfg.max_retries)),
                retry_backoff_factor=cast(float, http_config.get("retry_backoff_factor", 0.5)),
                verify_ssl=http_cfg.verify_ssl,
                ca_cert_path=http_cfg.ca_cert_path,
                extra_headers=cast(dict[str, str], http_config.get("headers", {})),
            )

        if not http_cfg.verify_ssl and not http_cfg.ca_cert_path:
            # A warning, not a field on an info line, because this setting
            # changed meaning. Until 2.5.0 it was accepted and discarded -- the
            # explicit transport silenced it -- so a `verify_ssl: false` left in
            # a configuration from that era did nothing, and now does exactly
            # what it says. Whoever wrote it may no longer be reading.
            logger.warning(
                "tls_verification_disabled",
                endpoint=endpoint,
                detail=(
                    "certificate verification is off for this upstream; anyone able to intercept the "
                    "connection can impersonate it. Set `tls.verify_ssl: true` (the default), or point "
                    "`tls.ca_cert_path` at the CA that signed it"
                ),
            )
        elif not http_cfg.verify_ssl and http_cfg.ca_cert_path:
            # `ca_cert_path` wins in HttpClient._create_client: it is assigned to
            # httpx's `verify=` and overrides the boolean, so verification is
            # ENFORCED against that CA regardless of `verify_ssl: false`. Warning
            # "verification is off" here is not just noise -- it points the
            # operator at the very setting doing the enforcing. Say what is true.
            logger.warning(
                "tls_verify_ssl_overridden_by_ca_cert",
                endpoint=endpoint,
                ca_cert_path=http_cfg.ca_cert_path,
                detail=(
                    "`tls.verify_ssl: false` is overridden: certificate verification is enforced "
                    "against the configured `tls.ca_cert_path`. If a handshake fails, the certificate "
                    "does not chain to that CA -- fix the CA bundle, do not expect verification to be off"
                ),
            )

        # Carry the registration-time SSRF policy onto the client config so the
        # connect-time guard (_SsrfGuardedTransport) re-applies the SAME policy
        # validate_no_ssrf used at registration -- closing DNS rebinding on the
        # connect path. HUMAN + None is the strict default; a DISCOVERY upstream
        # passes its runtime-reported addresses so its legitimate private
        # container IP is still reachable (without them it would be refused).
        http_cfg = replace(
            http_cfg,
            enforce_ssrf=enforce_ssrf,
            provenance=provenance,
            runtime_addresses=runtime_addresses,
        )

        logger.info(
            f"Connecting to HTTP mcp_server: {endpoint}",
            auth_type=auth.auth_type.value,
            verify_ssl=http_cfg.verify_ssl,
        )

        try:
            client = HttpClient(
                endpoint=endpoint,
                auth_config=auth,
                http_config=http_cfg,
            )
            return client
        except (OSError, ConnectionError, TimeoutError) as e:
            raise McpServerStartError(
                mcp_server_id="unknown",
                reason=f"Failed to connect to HTTP mcp_server: {e}",
                details={"endpoint": endpoint},
            ) from e
