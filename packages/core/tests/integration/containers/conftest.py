"""Container fixtures for integration tests.

Provides Testcontainers-based fixtures for:
- PostgreSQL (persistence layer)
- Redis (caching, rate limiting)
- Langfuse (LLM observability)
- Prometheus (metrics)
- Custom MCP Provider containers

Usage:
    pytest --run-containers tests/integration/containers/
"""

import time
from collections.abc import Generator
from typing import Any

import pytest

# Check if testcontainers is available
try:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs
    from testcontainers.postgres import PostgresContainer

    TESTCONTAINERS_AVAILABLE = True
except ImportError:
    TESTCONTAINERS_AVAILABLE = False
    PostgresContainer = None
    DockerContainer = None
    wait_for_logs = None

# Check if httpx is available for API testing
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None


def skip_if_no_testcontainers():
    """Skip test if testcontainers is not installed."""
    if not TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed. Run: pip install mcp-hangar[testcontainers]")


def skip_if_no_httpx():
    """Skip test if httpx is not installed."""
    if not HTTPX_AVAILABLE:
        pytest.skip("httpx not installed. Run: pip install httpx")


# ============================================================================
# Container Classes (only defined if testcontainers is available)
# ============================================================================

if TESTCONTAINERS_AVAILABLE:

    class RedisContainer(DockerContainer):
        """Redis container for caching and rate limiting tests."""

        def __init__(self, image: str = "redis:7-alpine", **kwargs):
            super().__init__(image=image, **kwargs)
            self.with_exposed_ports(6379)

        def get_connection_url(self) -> str:
            host = self.get_container_host_ip()
            port = self.get_exposed_port(6379)
            return f"redis://{host}:{port}/0"

    class LangfuseContainer(DockerContainer):
        """Langfuse container for LLM observability tests."""

        def __init__(self, image: str = "langfuse/langfuse:2", postgres_dsn: str | None = None, **kwargs):
            super().__init__(image=image, **kwargs)
            self.with_exposed_ports(3000)

            # Use internal postgres if not provided
            self._needs_postgres = postgres_dsn is None

            self.with_env("NEXTAUTH_SECRET", "test-secret-for-ci-testing")
            self.with_env("NEXTAUTH_URL", "http://localhost:3000")
            self.with_env("SALT", "test-salt-for-ci-testing")
            self.with_env("TELEMETRY_ENABLED", "false")

            # Test API keys
            self.with_env("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "pk-lf-test-key")
            self.with_env("LANGFUSE_INIT_PROJECT_SECRET_KEY", "sk-lf-test-key")

            if postgres_dsn:
                self.with_env("DATABASE_URL", postgres_dsn)

        def get_api_url(self) -> str:
            host = self.get_container_host_ip()
            port = self.get_exposed_port(3000)
            return f"http://{host}:{port}"

        @property
        def public_key(self) -> str:
            return "pk-lf-test-key"

        @property
        def secret_key(self) -> str:
            return "sk-lf-test-key"

    class PrometheusContainer(DockerContainer):
        """Prometheus container for metrics tests."""

        def __init__(self, image: str = "prom/prometheus:v2.47.0", **kwargs):
            super().__init__(image=image, **kwargs)
            self.with_exposed_ports(9090)
            self.with_command(
                "--config.file=/etc/prometheus/prometheus.yml",
                "--web.enable-lifecycle",
            )

        def get_api_url(self) -> str:
            host = self.get_container_host_ip()
            port = self.get_exposed_port(9090)
            return f"http://{host}:{port}"

    class MCPProviderContainer(DockerContainer):
        """Generic MCP Provider container for testing."""

        def __init__(self, image: str, provider_name: str, port: int = 8080, **kwargs):
            super().__init__(image=image, **kwargs)
            self.provider_name = provider_name
            self._port = port
            self.with_exposed_ports(port)

        def get_mcp_url(self) -> str:
            host = self.get_container_host_ip()
            port = self.get_exposed_port(self._port)
            return f"http://{host}:{port}"


# ============================================================================
# PostgreSQL Container
# ============================================================================


@pytest.fixture(scope="session")
def postgres_container() -> Generator[dict[str, Any], None, None]:
    """Start PostgreSQL container for database tests.

    Yields:
        Connection details dict with keys: host, port, user, password, database, dsn
    """
    skip_if_no_testcontainers()

    container = PostgresContainer(
        image="postgres:15-alpine",
        user="mcp_test",
        password="mcp_test_password",
        dbname="mcp_hangar_test",
    )

    with container:
        connection_info = {
            "host": container.get_container_host_ip(),
            "port": container.get_exposed_port(5432),
            "user": "mcp_test",
            "password": "mcp_test_password",
            "database": "mcp_hangar_test",
            "dsn": container.get_connection_url(),
        }
        yield connection_info


@pytest.fixture
def postgres_dsn(postgres_container: dict) -> str:
    """Get PostgreSQL DSN from container."""
    return postgres_container["dsn"]


# ============================================================================
# Redis Container
# ============================================================================


@pytest.fixture(scope="session")
def redis_container() -> Generator[dict[str, Any], None, None]:
    """Start Redis container for caching tests.

    Yields:
        Connection details dict with keys: host, port, url
    """
    skip_if_no_testcontainers()

    container = RedisContainer()

    with container:
        # Wait for Redis to be ready
        time.sleep(1)

        connection_info = {
            "host": container.get_container_host_ip(),
            "port": container.get_exposed_port(6379),
            "url": container.get_connection_url(),
        }
        yield connection_info


# ============================================================================
# Langfuse Container (with PostgreSQL)
# ============================================================================


@pytest.fixture(scope="session")
def langfuse_container(postgres_container: dict) -> Generator[dict[str, Any], None, None]:
    """Start Langfuse container for observability tests.

    Requires postgres_container fixture.

    Yields:
        Connection details dict with keys: host, port, url, public_key, secret_key
    """
    skip_if_no_testcontainers()

    # Build postgres DSN for Langfuse
    pg = postgres_container
    postgres_dsn = f"postgresql://{pg['user']}:{pg['password']}@{pg['host']}:{pg['port']}/{pg['database']}"

    container = LangfuseContainer(postgres_dsn=postgres_dsn)

    with container:
        # Wait for Langfuse to be ready
        try:
            wait_for_logs(container, "Ready", timeout=60)
        except Exception:
            # Fallback: wait and check health endpoint
            time.sleep(10)

        connection_info = {
            "host": container.get_container_host_ip(),
            "port": container.get_exposed_port(3000),
            "url": container.get_api_url(),
            "public_key": container.public_key,
            "secret_key": container.secret_key,
        }
        yield connection_info


@pytest.fixture
def langfuse_config(langfuse_container: dict):
    """Create LangfuseConfig from container."""
    from mcp_hangar.infrastructure.observability.langfuse_adapter import LangfuseConfig

    return LangfuseConfig(
        enabled=True,
        host=langfuse_container["url"],
        public_key=langfuse_container["public_key"],
        secret_key=langfuse_container["secret_key"],
    )


# ============================================================================
# Prometheus Container
# ============================================================================


@pytest.fixture(scope="session")
def prometheus_container() -> Generator[dict[str, Any], None, None]:
    """Start Prometheus container for metrics tests.

    Yields:
        Connection details dict with keys: host, port, url
    """
    skip_if_no_testcontainers()

    container = PrometheusContainer()

    with container:
        # Wait for Prometheus to be ready
        time.sleep(3)

        connection_info = {
            "host": container.get_container_host_ip(),
            "port": container.get_exposed_port(9090),
            "url": container.get_api_url(),
        }
        yield connection_info


# ============================================================================
# MCP Provider Containers
# ============================================================================


@pytest.fixture
def math_provider_container() -> Generator[dict[str, Any], None, None]:
    """Start math provider container for tool invocation tests."""
    skip_if_no_testcontainers()

    container = MCPProviderContainer(
        image="localhost/mcp-math:latest",
        provider_name="math",
        port=8080,
    )

    try:
        with container:
            time.sleep(2)

            yield {
                "name": "math",
                "host": container.get_container_host_ip(),
                "port": container.get_exposed_port(8080),
                "url": container.get_mcp_url(),
            }
    except Exception as e:
        pytest.skip(f"Math provider container not available: {e}")


@pytest.fixture
def sqlite_provider_container() -> Generator[dict[str, Any], None, None]:
    """Start SQLite provider container for database tool tests."""
    skip_if_no_testcontainers()

    container = MCPProviderContainer(
        image="localhost/mcp-sqlite:latest",
        provider_name="sqlite",
        port=8080,
    )
    container.with_volume_mapping("/tmp/mcp-test-data", "/data", "rw")

    try:
        with container:
            time.sleep(2)

            yield {
                "name": "sqlite",
                "host": container.get_container_host_ip(),
                "port": container.get_exposed_port(8080),
                "url": container.get_mcp_url(),
            }
    except Exception as e:
        pytest.skip(f"SQLite provider container not available: {e}")


# ============================================================================
# HTTP Client Fixtures
# ============================================================================


@pytest.fixture
def http_client() -> Generator[Any, None, None]:
    """Provide httpx client for API testing."""
    skip_if_no_httpx()

    with httpx.Client(timeout=30.0) as client:
        yield client


@pytest.fixture
def async_http_client() -> Generator[Any, None, None]:
    """Provide async httpx client for API testing."""
    skip_if_no_httpx()

    # This is a sync fixture that provides async client
    # Use with pytest-asyncio
    client = httpx.AsyncClient(timeout=30.0)
    yield client
    client.close()
