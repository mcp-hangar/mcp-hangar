"""Tests for domain value objects."""

import uuid

import pytest

from mcp_hangar.domain.value_objects import (
    CommandLine,
    CorrelationId,
    DockerImage,
    Endpoint,
    EnvironmentVariables,
    HealthCheckInterval,
    HttpAuthConfig,
    HttpAuthType,
    HttpTlsConfig,
    HttpTransportConfig,
    IdleTTL,
    MaxConsecutiveFailures,
    ProviderId,
    ProviderMode,
    TimeoutSeconds,
    ToolName,
)

# ProviderId Tests


def test_provider_id_valid():
    """Test valid provider IDs."""
    pid = ProviderId("math-provider")
    assert pid.value == "math-provider"
    assert str(pid) == "math-provider"


def test_provider_id_alphanumeric_underscore():
    """Test provider ID with alphanumeric and underscore."""
    pid = ProviderId("math_provider_123")
    assert pid.value == "math_provider_123"


def test_provider_id_empty():
    """Test that empty provider ID raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        ProviderId("")


def test_provider_id_too_long():
    """Test that provider ID longer than 64 characters raises ValueError."""
    with pytest.raises(ValueError, match="cannot exceed 64 characters"):
        ProviderId("a" * 65)


def test_provider_id_invalid_characters():
    """Test that provider ID with invalid characters raises ValueError."""
    with pytest.raises(ValueError, match="must contain only alphanumeric"):
        ProviderId("math provider")  # space not allowed


def test_provider_id_equality():
    """Test provider ID equality."""
    pid1 = ProviderId("math-provider")
    pid2 = ProviderId("math-provider")
    pid3 = ProviderId("other-provider")

    assert pid1 == pid2
    assert pid1 != pid3
    assert pid1 == "math-provider"  # String comparison supported for convenience


def test_provider_id_hashable():
    """Test that provider ID is hashable."""
    pid1 = ProviderId("math-provider")
    pid2 = ProviderId("math-provider")
    pid3 = ProviderId("other-provider")

    assert hash(pid1) == hash(pid2)
    assert hash(pid1) != hash(pid3)

    # Can be used in sets and dicts
    provider_set = {pid1, pid2, pid3}
    assert len(provider_set) == 2


# ToolName Tests


def test_tool_name_valid():
    """Test valid tool name."""
    tool = ToolName("calculate")
    assert tool.value == "calculate"
    assert str(tool) == "calculate"


def test_tool_name_empty():
    """Test that empty tool name raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        ToolName("")


def test_tool_name_too_long():
    """Test that tool name longer than 128 characters raises ValueError."""
    with pytest.raises(ValueError, match="cannot exceed 128 characters"):
        ToolName("a" * 129)


def test_tool_name_invalid_characters():
    """Test that tool name with invalid characters raises ValueError."""
    with pytest.raises(ValueError, match="must contain only alphanumeric"):
        ToolName("calculate!")


# ProviderMode Tests


def test_provider_mode_values():
    """Test provider mode enum values."""
    assert ProviderMode.SUBPROCESS.value == "subprocess"
    assert ProviderMode.DOCKER.value == "docker"
    assert ProviderMode.REMOTE.value == "remote"


def test_provider_mode_string():
    """Test provider mode string representation."""
    assert str(ProviderMode.SUBPROCESS) == "subprocess"
    assert str(ProviderMode.DOCKER) == "docker"


# CommandLine Tests


def test_command_line_basic():
    """Test basic command line."""
    cmd = CommandLine("python", "-m", "math_provider")
    assert cmd.command == "python"
    assert cmd.arguments == ("-m", "math_provider")
    assert cmd.to_list() == ["python", "-m", "math_provider"]


def test_command_line_no_arguments():
    """Test command line with no arguments."""
    cmd = CommandLine("python")
    assert cmd.command == "python"
    assert cmd.arguments == ()
    assert cmd.to_list() == ["python"]


def test_command_line_from_list():
    """Test creating command line from list."""
    cmd = CommandLine.from_list(["python", "-m", "math_provider"])
    assert cmd.command == "python"
    assert cmd.arguments == ("-m", "math_provider")


def test_command_line_from_empty_list():
    """Test that creating from empty list raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        CommandLine.from_list([])


def test_command_line_empty_command():
    """Test that empty command raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        CommandLine("")


def test_command_line_immutable():
    """Test that command line is immutable."""
    cmd = CommandLine("python", "-m", "math_provider")
    with pytest.raises(AttributeError):
        cmd.command = "node"


def test_command_line_string():
    """Test command line string representation."""
    cmd = CommandLine("python", "-m", "math_provider")
    assert str(cmd) == "python -m math_provider"


# DockerImage Tests


def test_docker_image_valid():
    """Test valid docker image."""
    img = DockerImage("python:3.11")
    assert img.value == "python:3.11"
    assert img.name == "python"
    assert img.tag == "3.11"


def test_docker_image_no_tag():
    """Test docker image without tag defaults to latest."""
    img = DockerImage("python")
    assert img.name == "python"
    assert img.tag == "latest"


def test_docker_image_with_registry():
    """Test docker image with registry."""
    img = DockerImage("ghcr.io/owner/image:v1.0")
    assert img.value == "ghcr.io/owner/image:v1.0"
    assert img.name == "ghcr.io/owner/image"
    assert img.tag == "v1.0"


def test_docker_image_empty():
    """Test that empty docker image raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        DockerImage("")


def test_docker_image_invalid_format():
    """Test that invalid docker image format raises ValueError."""
    with pytest.raises(ValueError, match="Invalid docker image format"):
        DockerImage("invalid image!")


# Endpoint Tests


def test_endpoint_http():
    """Test HTTP endpoint."""
    ep = Endpoint("http://localhost:8080")
    assert ep.value == "http://localhost:8080"
    assert ep.scheme == "http"
    assert ep.host == "localhost:8080"


def test_endpoint_https():
    """Test HTTPS endpoint."""
    ep = Endpoint("https://api.example.com/mcp")
    assert ep.scheme == "https"
    assert ep.host == "api.example.com"


def test_endpoint_websocket():
    """Test WebSocket endpoint."""
    ep = Endpoint("ws://localhost:9000")
    assert ep.scheme == "ws"


def test_endpoint_secure_websocket():
    """Test secure WebSocket endpoint."""
    ep = Endpoint("wss://api.example.com/ws")
    assert ep.scheme == "wss"


def test_endpoint_empty():
    """Test that empty endpoint raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        Endpoint("")


def test_endpoint_no_scheme():
    """Test that endpoint without scheme raises ValueError."""
    with pytest.raises(ValueError, match="must include scheme"):
        Endpoint("localhost:8080")


def test_endpoint_unsupported_scheme():
    """Test that unsupported scheme raises ValueError."""
    with pytest.raises(ValueError, match="Unsupported endpoint scheme"):
        Endpoint("ftp://localhost:21")


def test_endpoint_no_host():
    """Test that endpoint without host raises ValueError."""
    with pytest.raises(ValueError, match="must include host"):
        Endpoint("http://")


# EnvironmentVariables Tests


def test_environment_variables_empty():
    """Test empty environment variables."""
    env = EnvironmentVariables()
    assert len(env) == 0
    assert env.to_dict() == {}


def test_environment_variables_with_values():
    """Test environment variables with values."""
    env = EnvironmentVariables({"PATH": "/usr/bin", "USER": "test"})
    assert len(env) == 2
    assert env["PATH"] == "/usr/bin"
    assert env.get("USER") == "test"
    assert "PATH" in env


def test_environment_variables_get_default():
    """Test get with default value."""
    env = EnvironmentVariables({"PATH": "/usr/bin"})
    assert env.get("MISSING", "default") == "default"
    assert env.get("PATH", "default") == "/usr/bin"


def test_environment_variables_empty_key():
    """Test that empty key raises ValueError."""
    with pytest.raises(ValueError, match="key cannot be empty"):
        EnvironmentVariables({"": "value"})


def test_environment_variables_immutable():
    """Test that environment variables dict is immutable."""
    env = EnvironmentVariables({"PATH": "/usr/bin"})
    env_dict = env.to_dict()
    env_dict["NEW"] = "value"

    # Original should be unchanged
    assert "NEW" not in env


# IdleTTL Tests


def test_idle_ttl_valid():
    """Test valid idle TTL."""
    ttl = IdleTTL(300)
    assert ttl.seconds == 300
    assert int(ttl) == 300
    assert str(ttl) == "300s"


def test_idle_ttl_zero():
    """Test that zero TTL raises ValueError."""
    with pytest.raises(ValueError, match="must be positive"):
        IdleTTL(0)


def test_idle_ttl_negative():
    """Test that negative TTL raises ValueError."""
    with pytest.raises(ValueError, match="must be positive"):
        IdleTTL(-10)


def test_idle_ttl_too_large():
    """Test that TTL exceeding 1 day raises ValueError."""
    with pytest.raises(ValueError, match="cannot exceed 86400"):
        IdleTTL(86401)


# HealthCheckInterval Tests


def test_health_check_interval_valid():
    """Test valid health check interval."""
    interval = HealthCheckInterval(60)
    assert interval.seconds == 60
    assert int(interval) == 60
    assert str(interval) == "60s"


def test_health_check_interval_too_small():
    """Test that interval less than 5 seconds raises ValueError."""
    with pytest.raises(ValueError, match="must be at least 5 seconds"):
        HealthCheckInterval(4)


def test_health_check_interval_too_large():
    """Test that interval exceeding 1 hour raises ValueError."""
    with pytest.raises(ValueError, match="cannot exceed 3600"):
        HealthCheckInterval(3601)


# MaxConsecutiveFailures Tests


def test_max_consecutive_failures_valid():
    """Test valid max consecutive failures."""
    max_failures = MaxConsecutiveFailures(3)
    assert max_failures.count == 3
    assert int(max_failures) == 3
    assert str(max_failures) == "3"


def test_max_consecutive_failures_zero():
    """Test that zero failures raises ValueError."""
    with pytest.raises(ValueError, match="must be positive"):
        MaxConsecutiveFailures(0)


def test_max_consecutive_failures_too_large():
    """Test that failures exceeding 100 raises ValueError."""
    with pytest.raises(ValueError, match="cannot exceed 100"):
        MaxConsecutiveFailures(101)


# CorrelationId Tests


def test_correlation_id_generated():
    """Test that correlation ID is generated if not provided."""
    cid = CorrelationId()
    assert cid.value
    # Should be valid UUID
    uuid.UUID(cid.value, version=4)


def test_correlation_id_provided():
    """Test correlation ID with provided value."""
    test_uuid = str(uuid.uuid4())
    cid = CorrelationId(test_uuid)
    assert cid.value == test_uuid


def test_correlation_id_invalid():
    """Test that invalid UUID raises ValueError."""
    with pytest.raises(ValueError, match="must be a valid UUID"):
        CorrelationId("not-a-uuid")


def test_correlation_id_empty():
    """Test that empty correlation ID raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        CorrelationId("")


def test_correlation_id_unique():
    """Test that generated correlation IDs are unique."""
    cid1 = CorrelationId()
    cid2 = CorrelationId()
    assert cid1.value != cid2.value


# TimeoutSeconds Tests


def test_timeout_seconds_valid():
    """Test valid timeout."""
    timeout = TimeoutSeconds(30)
    assert timeout.seconds == 30.0
    assert float(timeout) == 30.0
    assert str(timeout) == "30.0s"


def test_timeout_seconds_float():
    """Test timeout with float value."""
    timeout = TimeoutSeconds(1.5)
    assert timeout.seconds == 1.5


def test_timeout_seconds_zero():
    """Test that zero timeout raises ValueError."""
    with pytest.raises(ValueError, match="must be positive"):
        TimeoutSeconds(0)


def test_timeout_seconds_negative():
    """Test that negative timeout raises ValueError."""
    with pytest.raises(ValueError, match="must be positive"):
        TimeoutSeconds(-5)


def test_timeout_seconds_too_large():
    """Test that timeout exceeding 1 hour raises ValueError."""
    with pytest.raises(ValueError, match="cannot exceed 3600"):
        TimeoutSeconds(3601)


# HttpAuthType Tests


def test_http_auth_type_values():
    """Test HttpAuthType enum values."""
    assert HttpAuthType.NONE.value == "none"
    assert HttpAuthType.API_KEY.value == "api_key"
    assert HttpAuthType.BEARER.value == "bearer"
    assert HttpAuthType.BASIC.value == "basic"


def test_http_auth_type_normalize():
    """Test HttpAuthType normalization."""
    assert HttpAuthType.normalize("api_key") == HttpAuthType.API_KEY
    assert HttpAuthType.normalize("bearer") == HttpAuthType.BEARER
    assert HttpAuthType.normalize(None) == HttpAuthType.NONE
    assert HttpAuthType.normalize(HttpAuthType.BASIC) == HttpAuthType.BASIC


# HttpAuthConfig Tests


def test_http_auth_config_no_auth():
    """Test HttpAuthConfig with no auth."""
    auth = HttpAuthConfig()
    assert auth.auth_type == HttpAuthType.NONE
    assert auth.get_headers() == {}


def test_http_auth_config_api_key():
    """Test HttpAuthConfig with API key."""
    auth = HttpAuthConfig(
        auth_type=HttpAuthType.API_KEY,
        api_key="secret-key",
        api_key_header="X-API-Key",
    )
    headers = auth.get_headers()
    assert headers == {"X-API-Key": "secret-key"}


def test_http_auth_config_api_key_custom_header():
    """Test HttpAuthConfig with custom API key header."""
    auth = HttpAuthConfig(
        auth_type=HttpAuthType.API_KEY,
        api_key="my-key",
        api_key_header="Authorization-Key",
    )
    headers = auth.get_headers()
    assert headers == {"Authorization-Key": "my-key"}


def test_http_auth_config_bearer():
    """Test HttpAuthConfig with bearer token."""
    auth = HttpAuthConfig(
        auth_type=HttpAuthType.BEARER,
        bearer_token="jwt-token",
    )
    headers = auth.get_headers()
    assert headers == {"Authorization": "Bearer jwt-token"}


def test_http_auth_config_basic():
    """Test HttpAuthConfig with basic auth."""
    auth = HttpAuthConfig(
        auth_type=HttpAuthType.BASIC,
        basic_username="user",
        basic_password="pass",
    )
    headers = auth.get_headers()
    # user:pass base64 encoded
    assert headers == {"Authorization": "Basic dXNlcjpwYXNz"}


def test_http_auth_config_api_key_required():
    """Test HttpAuthConfig requires api_key for API_KEY type."""
    with pytest.raises(ValueError, match="api_key is required"):
        HttpAuthConfig(auth_type=HttpAuthType.API_KEY)


def test_http_auth_config_bearer_token_required():
    """Test HttpAuthConfig requires bearer_token for BEARER type."""
    with pytest.raises(ValueError, match="bearer_token is required"):
        HttpAuthConfig(auth_type=HttpAuthType.BEARER)


def test_http_auth_config_basic_credentials_required():
    """Test HttpAuthConfig requires both username and password for BASIC type."""
    with pytest.raises(ValueError, match="basic_username and basic_password"):
        HttpAuthConfig(auth_type=HttpAuthType.BASIC, basic_username="user")

    with pytest.raises(ValueError, match="basic_username and basic_password"):
        HttpAuthConfig(auth_type=HttpAuthType.BASIC, basic_password="pass")


def test_http_auth_config_from_dict_none():
    """Test HttpAuthConfig.from_dict with None."""
    auth = HttpAuthConfig.from_dict(None)
    assert auth.auth_type == HttpAuthType.NONE


def test_http_auth_config_from_dict_bearer():
    """Test HttpAuthConfig.from_dict with bearer config."""
    auth = HttpAuthConfig.from_dict(
        {
            "type": "bearer",
            "bearer_token": "my-token",
        }
    )
    assert auth.auth_type == HttpAuthType.BEARER
    assert auth.bearer_token == "my-token"


def test_http_auth_config_from_dict_basic():
    """Test HttpAuthConfig.from_dict with basic auth config."""
    auth = HttpAuthConfig.from_dict(
        {
            "type": "basic",
            "username": "admin",
            "password": "secret",
        }
    )
    assert auth.auth_type == HttpAuthType.BASIC
    assert auth.basic_username == "admin"
    assert auth.basic_password == "secret"


# HttpTlsConfig Tests


def test_http_tls_config_defaults():
    """Test HttpTlsConfig defaults."""
    tls = HttpTlsConfig()
    assert tls.verify_ssl is True
    assert tls.ca_cert_path is None


def test_http_tls_config_custom():
    """Test HttpTlsConfig with custom values."""
    tls = HttpTlsConfig(verify_ssl=False, ca_cert_path="/path/to/ca.pem")
    assert tls.verify_ssl is False
    assert tls.ca_cert_path == "/path/to/ca.pem"


def test_http_tls_config_from_dict():
    """Test HttpTlsConfig.from_dict."""
    tls = HttpTlsConfig.from_dict(
        {
            "verify_ssl": False,
            "ca_cert_path": "/custom/ca.pem",
        }
    )
    assert tls.verify_ssl is False
    assert tls.ca_cert_path == "/custom/ca.pem"


def test_http_tls_config_from_dict_none():
    """Test HttpTlsConfig.from_dict with None."""
    tls = HttpTlsConfig.from_dict(None)
    assert tls.verify_ssl is True


# HttpTransportConfig Tests


def test_http_transport_config_defaults():
    """Test HttpTransportConfig defaults."""
    config = HttpTransportConfig()
    assert config.connect_timeout == 10.0
    assert config.read_timeout == 30.0
    assert config.max_retries == 3
    assert config.retry_backoff_factor == 0.5
    assert config.keep_alive is True


def test_http_transport_config_custom():
    """Test HttpTransportConfig with custom values."""
    config = HttpTransportConfig(
        connect_timeout=5.0,
        read_timeout=60.0,
        max_retries=5,
        retry_backoff_factor=1.0,
        keep_alive=False,
        extra_headers={"X-Custom": "value"},
    )
    assert config.connect_timeout == 5.0
    assert config.read_timeout == 60.0
    assert config.max_retries == 5
    assert config.extra_headers == {"X-Custom": "value"}


def test_http_transport_config_validation():
    """Test HttpTransportConfig validation."""
    with pytest.raises(ValueError, match="connect_timeout must be positive"):
        HttpTransportConfig(connect_timeout=0)

    with pytest.raises(ValueError, match="read_timeout must be positive"):
        HttpTransportConfig(read_timeout=-1)

    with pytest.raises(ValueError, match="max_retries cannot be negative"):
        HttpTransportConfig(max_retries=-1)


def test_http_transport_config_from_dict():
    """Test HttpTransportConfig.from_dict."""
    config = HttpTransportConfig.from_dict(
        {
            "connect_timeout": 15.0,
            "read_timeout": 45.0,
            "max_retries": 10,
            "headers": {"Authorization": "Bearer token"},
        }
    )
    assert config.connect_timeout == 15.0
    assert config.read_timeout == 45.0
    assert config.max_retries == 10
    assert config.extra_headers == {"Authorization": "Bearer token"}


def test_http_transport_config_from_dict_none():
    """Test HttpTransportConfig.from_dict with None returns defaults."""
    config = HttpTransportConfig.from_dict(None)
    assert config.connect_timeout == 10.0
    assert config.max_retries == 3
