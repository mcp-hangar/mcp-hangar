"""HTTP provider launcher implementation."""

from ....logging_config import get_logger
from ...exceptions import ProviderStartError, ValidationError
from ...security.input_validator import InputValidator
from .base import ProviderLauncher

logger = get_logger(__name__)


class HttpLauncher(ProviderLauncher):
    """
    Launcher for remote HTTP-based MCP providers.

    Connects to MCP providers exposed via HTTP/HTTPS endpoints.
    Supports:
    - Multiple authentication schemes (none, API key, bearer token, basic)
    - SSE (Server-Sent Events) streaming
    - TLS with custom CA certificates
    - Connection pooling and retry logic

    Note: This launcher does not start a process - it creates a client
    that connects to an already-running remote provider.
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
        auth_config: dict | None = None,
        tls_config: dict | None = None,
        http_config: dict | None = None,
    ):
        """
        Create an HTTP client for a remote MCP provider.

        Args:
            endpoint: HTTP/HTTPS URL of the MCP provider.
            auth_config: Authentication configuration dict.
            tls_config: TLS configuration dict.
            http_config: HTTP transport configuration dict.

        Returns:
            HttpClient connected to the remote provider.

        Raises:
            ValidationError: If inputs fail validation.
            ProviderStartError: If connection cannot be established.
        """
        # Validate endpoint
        self._validate_endpoint(endpoint)

        # Import here to avoid circular imports
        from ....http_client import AuthConfig, AuthType, HttpClient, HttpClientConfig

        # Build auth config
        auth = AuthConfig()
        if auth_config:
            auth_type_str = auth_config.get("type", "none")
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
                api_key=auth_config.get("api_key"),
                api_key_header=auth_config.get("api_key_header", "X-API-Key"),
                bearer_token=auth_config.get("bearer_token"),
                basic_username=auth_config.get("username"),
                basic_password=auth_config.get("password"),
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
                verify_ssl=tls_config.get("verify_ssl", True),
                ca_cert_path=tls_config.get("ca_cert_path"),
            )

        if http_config:
            http_cfg = HttpClientConfig(
                connect_timeout=http_config.get("connect_timeout", http_cfg.connect_timeout),
                read_timeout=http_config.get("read_timeout", http_cfg.read_timeout),
                max_retries=http_config.get("max_retries", http_cfg.max_retries),
                retry_backoff_factor=http_config.get("retry_backoff_factor", 0.5),
                verify_ssl=http_cfg.verify_ssl,
                ca_cert_path=http_cfg.ca_cert_path,
                extra_headers=http_config.get("headers", {}),
            )

        logger.info(
            f"Connecting to HTTP provider: {endpoint}",
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
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"Failed to connect to HTTP provider: {e}",
                details={"endpoint": endpoint},
            ) from e
