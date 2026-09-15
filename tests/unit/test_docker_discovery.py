"""Unit tests for Docker discovery reconnection and backoff.

Tests DockerDiscoverySource automatic reconnection with exponential
backoff when Docker daemon connection is lost.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# Patch docker module availability for all tests in this module
@pytest.fixture(autouse=True)
def _mock_docker_available():
    """Ensure DOCKER_AVAILABLE is True and docker module is mocked."""
    with patch("mcp_hangar.infrastructure.discovery.docker_source.DOCKER_AVAILABLE", True):
        with patch("mcp_hangar.infrastructure.discovery.docker_source.docker") as mock_docker:
            # DockerClient is a MagicMock so .return_value works properly
            mock_docker.DockerClient = MagicMock()
            mock_docker.from_env = MagicMock()
            # Patch find_container_socket to return None so from_env path is used
            with patch(
                "mcp_hangar.infrastructure.discovery.docker_source.find_container_socket",
                return_value=None,
            ):
                yield mock_docker


@pytest.fixture
def mock_docker(_mock_docker_available):
    """Provide the mocked docker module."""
    return _mock_docker_available


def _no_backoff(source):
    """Skip the retry backoff: the wait returns at once, with no stop requested."""
    return patch.object(source._stop_requested, "wait", return_value=False)


class TestEnsureClientRetry:
    """Tests for the connection retry schedule, with exponential backoff."""

    def test_ensure_client_retries_on_failure(self, mock_docker):
        """_ensure_client() retries connection up to max_retries on failure."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=3, initial_backoff_s=0.01)

        with _no_backoff(source):
            with pytest.raises(DockerException, match="Failed to connect.*3 attempts"):
                source._connect_with_retries()

    def test_ensure_client_succeeds_on_retry(self, mock_docker):
        """_ensure_client() succeeds on retry after initial failure (daemon restart)."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        fail_client = MagicMock()
        fail_client.ping.side_effect = DockerException("Connection refused")

        ok_client = MagicMock()
        ok_client.ping.return_value = True

        # First call fails, second succeeds
        mock_docker.from_env.side_effect = [fail_client, ok_client]

        source = DockerDiscoverySource(max_retries=5, initial_backoff_s=0.01)

        with _no_backoff(source):
            assert source._connect_with_retries() is ok_client
        fail_client.close.assert_called_once()

    def test_ensure_client_raises_after_max_retries(self, mock_docker):
        """_ensure_client() raises after exhausting max_retries."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=2, initial_backoff_s=0.01)

        with _no_backoff(source):
            with pytest.raises(DockerException, match="Failed to connect.*2 attempts"):
                source._connect_with_retries()

    async def test_ensure_client_skips_when_already_connected(self, mock_docker):
        """_ensure_client() is a no-op when client is already connected."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource()
        existing_client = MagicMock()
        source._client = existing_client

        await source._ensure_client()

        assert source._client is existing_client
        # from_env should not have been called
        mock_docker.from_env.assert_not_called()


class TestBackoffTiming:
    """Tests for exponential backoff delay calculation."""

    def test_backoff_increases_exponentially(self, mock_docker):
        """Backoff delays increase exponentially (1s, 2s, 4s, 8s, ...)."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(
            max_retries=5,
            initial_backoff_s=1.0,
            max_backoff_s=30.0,
        )

        sleep_calls = []
        with patch.object(source._stop_requested, "wait", side_effect=lambda t: sleep_calls.append(t)):
            with pytest.raises(DockerException):
                source._connect_with_retries()

        # Should have (max_retries - 1) sleeps = 4
        assert len(sleep_calls) == 4

        # Verify exponential increase (with jitter tolerance)
        # Expected base delays: 1, 2, 4, 8
        for i, actual in enumerate(sleep_calls):
            expected_base = 1.0 * (2**i)
            # Allow 15% jitter tolerance (jitter is +/-10%)
            assert actual >= expected_base * 0.85, f"Sleep {i}: {actual} < {expected_base * 0.85}"
            assert actual <= expected_base * 1.15, f"Sleep {i}: {actual} > {expected_base * 1.15}"

    def test_backoff_capped_at_max(self, mock_docker):
        """Backoff delay is capped at max_backoff_s."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(
            max_retries=6,
            initial_backoff_s=1.0,
            max_backoff_s=5.0,
        )

        sleep_calls = []
        with patch.object(source._stop_requested, "wait", side_effect=lambda t: sleep_calls.append(t)):
            with pytest.raises(DockerException):
                source._connect_with_retries()

        # After reaching max (5s), all subsequent should be capped
        for s in sleep_calls:
            assert s <= 5.0 * 1.15  # With jitter tolerance


class TestDiscoverReconnection:
    """Tests for discover() reconnection behavior."""

    @pytest.mark.asyncio
    async def test_discover_returns_empty_on_connection_failure(self, mock_docker):
        """discover() returns empty list (not raises) when reconnection fails."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=2, initial_backoff_s=0.01)

        with _no_backoff(source):
            result = await source.discover()

        assert result == []

    @pytest.mark.asyncio
    async def test_discover_resets_client_on_connection_error(self, mock_docker):
        """discover() catches connection error, resets _client to None, and retries."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        # First: client connects OK
        ok_client = MagicMock()
        ok_client.ping.return_value = True
        ok_client.containers.list.side_effect = DockerException("Connection lost")

        mock_docker.from_env.return_value = ok_client

        source = DockerDiscoverySource(max_retries=2, initial_backoff_s=0.01)

        with _no_backoff(source):
            result = await source.discover()

        # Should have reset client to None for reconnection on next call
        assert source._client is None
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_works_normally_after_reconnection(self, mock_docker):
        """After successful reconnection, subsequent discover() calls work normally."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        # Setup: first call will fail, second will succeed
        fail_client = MagicMock()
        fail_client.ping.side_effect = DockerException("Dead")

        ok_client = MagicMock()
        ok_client.ping.return_value = True
        mock_container = MagicMock()
        mock_container.id = "abc123def456789"
        mock_container.name = "test-provider"
        mock_container.status = "running"
        mock_container.labels = {
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "test-provider",
            "mcp.hangar.mode": "container",
        }
        mock_container.image = MagicMock()
        mock_container.image.tags = ["test:latest"]
        mock_container.image.id = "sha256:abc123def456"
        ok_client.containers.list.return_value = [mock_container]

        # First call: fail to connect -> empty list
        mock_docker.from_env.side_effect = [fail_client, fail_client, ok_client]

        source = DockerDiscoverySource(max_retries=2, initial_backoff_s=0.01)

        with _no_backoff(source):
            result1 = await source.discover()

        assert result1 == []

        # Second call: reconnect and succeed
        mock_docker.from_env.side_effect = [ok_client]
        with _no_backoff(source):
            result2 = await source.discover()

        assert len(result2) == 1
        assert result2[0].name == "test-provider"


class TestContainerIdTracking:
    """Tests for container ID tracking to prevent duplicates."""

    @pytest.mark.asyncio
    async def test_container_ids_tracked(self, mock_docker):
        """Containers are tracked by ID to prevent duplicates after reconnection."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.return_value = True

        container1 = MagicMock()
        container1.id = "aaa111bbb222ccc"
        container1.name = "provider-1"
        container1.status = "running"
        container1.labels = {
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "provider-1",
            "mcp.hangar.mode": "container",
        }
        container1.image = MagicMock()
        container1.image.tags = ["img:latest"]
        container1.image.id = "sha256:abc123def456"

        container2 = MagicMock()
        container2.id = "ddd444eee555fff"
        container2.name = "provider-2"
        container2.status = "running"
        container2.labels = {
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "provider-2",
            "mcp.hangar.mode": "container",
        }
        container2.image = MagicMock()
        container2.image.tags = ["img2:latest"]
        container2.image.id = "sha256:def456abc789"

        mock_client.containers.list.return_value = [container1, container2]
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)

        result = await source.discover()

        assert len(result) == 2
        assert source._known_container_ids == {"aaa111bbb222", "ddd444eee555"}


class TestHealthCheck:
    """Tests for health_check() behavior with connection errors."""

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_connection_error(self, mock_docker):
        """health_check() returns False on connection error (existing behavior preserved)."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionError("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)

        result = await source.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_healthy(self, mock_docker):
        """health_check() returns True once discovery has reached the daemon."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.containers.list.return_value = []
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)

        assert await source.health_check() is False  # nothing known yet
        await source.discover()

        assert await source.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_reports_a_lost_connection(self, mock_docker):
        """A scan that loses the daemon turns health_check() False, without a probe of its own."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.containers.list.side_effect = DockerException("Connection lost")
        mock_docker.from_env.return_value = mock_client
        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)

        await source.discover()
        pings = mock_client.ping.call_count

        assert await source.health_check() is False
        assert mock_client.ping.call_count == pings


def _make_http_container(
    *,
    name: str = "math-provider",
    mode: str = "http",
    port_label: str = "8080",
    ports: dict | None = None,
    networks: dict | None = None,
):
    """Build a MagicMock container labeled for http/sse discovery.

    Args:
        ports: NetworkSettings.Ports mapping (published host bindings).
        networks: NetworkSettings.Networks mapping (internal bridge IPs).
    """
    container = MagicMock()
    container.id = "abc123def456789"
    container.name = name
    container.status = "running"
    container.labels = {
        "mcp.hangar.enabled": "true",
        "mcp.hangar.name": name,
        "mcp.hangar.mode": mode,
        "mcp.hangar.port": port_label,
    }
    container.image = MagicMock()
    container.image.tags = ["math-provider:test"]
    container.image.id = "sha256:abc123def456"
    container.attrs = {
        "NetworkSettings": {
            "Ports": ports or {},
            "Networks": networks or {},
        }
    }
    return container


class TestGetHostEndpoint:
    """Tests for _get_host_endpoint() -- issue #481.

    http/sse discovery must prefer the published host-port binding over the
    container's internal bridge-network IP, since the documented deployment
    runs Hangar on the host (unreachable from the container's bridge
    network, e.g. Podman-on-macOS).
    """

    def test_prefers_published_host_port_over_bridge_ip(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(
            ports={"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]},
            networks={"bridge": {"IPAddress": "10.88.0.23"}},
        )

        source = DockerDiscoverySource()
        result = source._get_host_endpoint(container, 8080)

        assert result == ("127.0.0.1", 18080)

    def test_normalizes_0000_host_ip_to_loopback(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(ports={"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]})

        source = DockerDiscoverySource()
        host, port = source._get_host_endpoint(container, 8080)

        assert host == "127.0.0.1"
        assert port == 18080

    def test_normalizes_ipv6_any_host_ip_to_loopback(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(ports={"8080/tcp": [{"HostIp": "::", "HostPort": "18080"}]})

        source = DockerDiscoverySource()
        host, port = source._get_host_endpoint(container, 8080)

        assert host == "127.0.0.1"
        assert port == 18080

    def test_preserves_explicit_host_ip_binding(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(ports={"8080/tcp": [{"HostIp": "192.168.1.50", "HostPort": "18080"}]})

        source = DockerDiscoverySource()
        host, port = source._get_host_endpoint(container, 8080)

        assert host == "192.168.1.50"
        assert port == 18080

    def test_falls_back_to_bridge_ip_when_no_host_binding(self, mock_docker):
        """No published host port (e.g. Hangar itself runs on the same network)."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(
            ports={},
            networks={"bridge": {"IPAddress": "10.88.0.23"}},
        )

        source = DockerDiscoverySource()
        host, port = source._get_host_endpoint(container, 8080)

        assert host == "10.88.0.23"
        assert port == 8080  # container-side port, since we're on its network

    def test_falls_back_when_port_binding_is_null(self, mock_docker):
        """Docker/Podman report `null` for exposed-but-unpublished ports."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(
            ports={"8080/tcp": None},
            networks={"bridge": {"IPAddress": "10.88.0.23"}},
        )

        source = DockerDiscoverySource()
        host, port = source._get_host_endpoint(container, 8080)

        assert host == "10.88.0.23"
        assert port == 8080

    def test_returns_none_when_no_host_binding_and_no_ip(self, mock_docker):
        """Starting/stopped container: no host port, no network IP yet."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(ports={}, networks={})

        source = DockerDiscoverySource()
        result = source._get_host_endpoint(container, 8080)

        assert result is None


class TestParseContainerHttpMode:
    """Tests for _parse_container() http/sse endpoint resolution -- issue #481."""

    def test_http_mode_endpoint_uses_published_host_port(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(
            mode="http",
            port_label="8080",
            ports={"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]},
            networks={"bridge": {"IPAddress": "10.88.0.23"}},
        )

        source = DockerDiscoverySource()
        result = source._parse_container(container)

        assert result is not None
        assert result.connection_info["host"] == "127.0.0.1"
        assert result.connection_info["port"] == 18080
        assert result.connection_info["endpoint"] == "http://127.0.0.1:18080"

    def test_sse_mode_endpoint_uses_published_host_port(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(
            mode="sse",
            port_label="9000",
            ports={"9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "29000"}]},
        )

        source = DockerDiscoverySource()
        result = source._parse_container(container)

        assert result is not None
        assert result.connection_info["endpoint"] == "http://127.0.0.1:29000"

    def test_http_mode_falls_back_to_bridge_ip_without_publish(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(
            mode="http",
            ports={},
            networks={"bridge": {"IPAddress": "10.88.0.23"}},
        )

        source = DockerDiscoverySource()
        result = source._parse_container(container)

        assert result is not None
        assert result.connection_info["endpoint"] == "http://10.88.0.23:8080"

    def test_http_mode_returns_none_when_unreachable(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(mode="http", ports={}, networks={})

        source = DockerDiscoverySource()
        result = source._parse_container(container)

        assert result is None

    def test_no_ip_skip_is_logged_at_debug_not_warning(self, mock_docker):
        """Issue #484: the expected/transient no-IP skip must log at debug, not warning."""
        from mcp_hangar.infrastructure.discovery import docker_source
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = _make_http_container(mode="http", ports={}, networks={})

        source = DockerDiscoverySource()
        with patch.object(docker_source, "logger") as mock_logger:
            result = source._parse_container(container)

        assert result is None
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_called_once()
        assert "no IP" in mock_logger.debug.call_args[0][0]

    def test_container_mode_unaffected_by_host_port_logic(self, mock_docker):
        """Container/stdio-mode discovery does not consult ports/IP at all."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        container = MagicMock()
        container.id = "aaa111bbb222ccc"
        container.name = "stdio-provider"
        container.status = "running"
        container.labels = {
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "stdio-provider",
            "mcp.hangar.mode": "container",
        }
        container.image = MagicMock()
        container.image.tags = ["stdio:latest"]
        container.image.id = "sha256:abc123def456"
        container.attrs = {"NetworkSettings": {"Ports": {}, "Networks": {}}}

        source = DockerDiscoverySource()
        result = source._parse_container(container)

        assert result is not None
        assert result.mode == "container"
        assert "host" not in result.connection_info
        assert "endpoint" not in result.connection_info


class TestInitConfiguration:
    """Tests for __init__ reconnection configuration."""

    def test_default_reconnection_params(self, mock_docker):
        """Default reconnection parameters are set correctly."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource()

        assert source._max_retries == 5
        assert source._initial_backoff_s == 1.0
        assert source._max_backoff_s == 30.0
        assert source._connect_timeout_s == 5.0
        assert source._known_container_ids == set()

    def test_custom_reconnection_params(self, mock_docker):
        """Custom reconnection parameters are accepted."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource(
            max_retries=10,
            initial_backoff_s=0.5,
            max_backoff_s=60.0,
        )

        assert source._max_retries == 10
        assert source._initial_backoff_s == 0.5
        assert source._max_backoff_s == 60.0


class TestAStopEndsTheRetryWait:
    """A stop reaches a connection retry that is waiting out its backoff (#1436).

    The retry runs on discovery's loop thread, so while it waits nothing else
    on that loop runs, the orchestrator's stop() included. The stop reaches it
    from the stopping thread, through request_stop(). Each backoff here is 30 s,
    so a wait that ran its course would fail the timing assertions.
    """

    def test_request_stop_ends_the_wait_and_the_retries(self, mock_docker):
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        attempted = threading.Event()

        def unreachable(**kwargs):
            attempted.set()
            raise DockerException("Connection refused")

        mock_docker.from_env.side_effect = unreachable
        source = DockerDiscoverySource(max_retries=5, initial_backoff_s=30.0)
        raised: list[Exception] = []

        def connect():
            try:
                source._connect_with_retries()
            except DockerException as e:
                raised.append(e)

        worker = threading.Thread(target=connect)
        worker.start()
        assert attempted.wait(5)

        started = time.monotonic()
        source.request_stop()
        worker.join(5)

        assert not worker.is_alive()
        assert time.monotonic() - started < 1.0
        assert len(raised) == 1
        assert "stopping" in str(raised[0])
        # No attempt after the stop.
        assert mock_docker.from_env.call_count == 1

    async def test_a_stopped_source_connects_again_only_once_started(self, mock_docker):
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        client = MagicMock()
        mock_docker.from_env.return_value = client
        source = DockerDiscoverySource(initial_backoff_s=30.0)

        await source.stop()
        with pytest.raises(DockerException, match="stopping"):
            await source._ensure_client()
        assert await source.discover() == []
        mock_docker.from_env.assert_not_called()

        await source.start()
        await source.discover()
        assert source._client is client

    def test_stopping_discovery_returns_while_docker_is_unreachable(self, mock_docker):
        """The served stop path: the loop is blocked in the backoff when the stop comes."""
        from docker.errors import DockerException

        from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource
        from mcp_hangar.server.lifecycle import start_discovery_loop, stop_discovery_loop

        # Docker answers the start, then goes away: the first cycle loses the
        # connection and the next one retries into the backoff.
        lost = MagicMock()
        lost.containers.list.side_effect = DockerException("Connection lost")
        answers = iter([lost])
        retrying = threading.Event()

        def from_env(**kwargs):
            client = next(answers, None)
            if client is not None:
                return client
            retrying.set()
            raise DockerException("Connection refused")

        mock_docker.from_env.side_effect = from_env
        orchestrator = DiscoveryOrchestrator(DiscoveryConfig(refresh_interval_s=0))
        orchestrator.add_source(DockerDiscoverySource(initial_backoff_s=30.0))

        loop, thread = start_discovery_loop(orchestrator)
        try:
            assert retrying.wait(5)
        finally:
            started = time.monotonic()
            stop_discovery_loop(orchestrator, loop, thread)

        assert time.monotonic() - started < 1.0
        assert not thread.is_alive()
        assert orchestrator.get_stats()["running"] is False


def _labeled_container():
    container = MagicMock()
    container.id = "abc123def456789"
    container.name = "test-provider"
    container.status = "running"
    container.labels = {
        "mcp.hangar.enabled": "true",
        "mcp.hangar.name": "test-provider",
        "mcp.hangar.mode": "container",
    }
    container.image = MagicMock()
    container.image.tags = ["test:latest"]
    container.image.id = "sha256:abc123def456"
    return container


class _HangingDaemon:
    """A `docker.from_env` that hangs until released, like a TCP DOCKER_HOST that never answers.

    It records the thread it hangs on, so a test can see that thread end. It
    gives up after 5 s on its own, so a regression fails instead of hanging.
    """

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.threads: list[threading.Thread] = []
        self.client = MagicMock()

    def __call__(self, **kwargs):
        self.threads.append(threading.current_thread())
        self.entered.set()
        self.release.wait(5)
        return self.client

    def finish(self):
        self.release.set()
        for thread in self.threads:
            thread.join(5)
            assert not thread.is_alive()


class TestDockerDoesNotHoldUpStartOrStop:
    """Connecting runs off discovery's loop, and a stop abandons it (#1464)."""

    async def test_start_returns_while_docker_is_unreachable(self, mock_docker):
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_docker.from_env.side_effect = DockerException("Connection refused")
        source = DockerDiscoverySource(initial_backoff_s=30.0)

        started = time.monotonic()
        await source.start()
        assert time.monotonic() - started < 1.0

        connecting = source._connecting
        assert connecting is not None
        await source.stop()
        # The stop ended the 30 s backoff on the connection's thread.
        with pytest.raises(DockerException, match="stopping"):
            connecting.result(timeout=1)

    def test_each_attempt_has_the_connect_timeout_and_the_client_keeps_its_default(self, mock_docker):
        from docker.constants import DEFAULT_TIMEOUT_SECONDS

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        client = MagicMock()
        mock_docker.from_env.return_value = client
        source = DockerDiscoverySource(connect_timeout_s=2.5)

        assert source._connect_with_retries() is client
        mock_docker.from_env.assert_called_once_with(timeout=2.5)
        assert client.api.timeout == DEFAULT_TIMEOUT_SECONDS

        with patch(
            "mcp_hangar.infrastructure.discovery.docker_source.find_container_socket",
            return_value="/run/test/docker.sock",
        ):
            source._connect_with_retries()
        mock_docker.DockerClient.assert_called_once_with(base_url="unix:///run/test/docker.sock", timeout=2.5)

    async def test_a_stop_abandons_an_attempt_the_daemon_never_answers(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        daemon = _HangingDaemon()
        mock_docker.from_env.side_effect = daemon
        source = DockerDiscoverySource(initial_backoff_s=30.0)
        try:
            await source.start()
            scan = asyncio.create_task(source.discover())
            assert await asyncio.to_thread(daemon.entered.wait, 5)

            started = time.monotonic()
            await source.stop()
            assert time.monotonic() - started < 1.0
            assert daemon.threads[0].is_alive()  # still in the attempt
        finally:
            daemon.finish()

        # The scan that was waiting gets nothing, and the client the abandoned
        # attempt produced after all is closed, not kept.
        assert await asyncio.wait_for(scan, 1) == []
        daemon.client.close.assert_called_once()
        assert source._client is None
        assert mock_docker.from_env.call_count == 1

    async def test_a_reachable_docker_is_discovered_by_the_first_scan_after_start(self, mock_docker):
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        client = MagicMock()
        client.containers.list.return_value = [_labeled_container()]
        mock_docker.from_env.return_value = client
        source = DockerDiscoverySource()

        await source.start()
        found = await source.discover()

        assert [server.name for server in found] == ["test-provider"]
        assert source._client is client
        assert await source.health_check() is True
        mock_docker.from_env.assert_called_once()

    def test_serving_starts_answers_status_and_stops_during_an_attempt_that_hangs(self, mock_docker):
        """The served path: a real orchestrator on start_discovery_loop, Docker never answering."""
        from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource
        from mcp_hangar.server.lifecycle import start_discovery_loop, stop_discovery_loop

        daemon = _HangingDaemon()
        mock_docker.from_env.side_effect = daemon
        orchestrator = DiscoveryOrchestrator(DiscoveryConfig(refresh_interval_s=0))
        orchestrator.add_source(DockerDiscoverySource(initial_backoff_s=30.0))

        try:
            started = time.monotonic()
            loop, thread = start_discovery_loop(orchestrator)
            assert time.monotonic() - started < 1.0
            assert daemon.entered.wait(5)

            # The listing runs on the serving loop; here, a loop of this thread's own.
            started = time.monotonic()
            statuses = asyncio.run(orchestrator.get_sources_status())
            assert time.monotonic() - started < 1.0
            assert [(s["source_type"], s["is_healthy"]) for s in statuses] == [("docker", False)]

            started = time.monotonic()
            stop_discovery_loop(orchestrator, loop, thread)
            assert time.monotonic() - started < 1.0
            assert not thread.is_alive()
            assert orchestrator.get_stats()["running"] is False
            assert daemon.threads[0].is_alive()  # abandoned mid-attempt
        finally:
            daemon.finish()

        daemon.client.close.assert_called_once()

    def test_serving_discovers_a_reachable_docker_in_the_first_cycle(self, mock_docker):
        from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource
        from mcp_hangar.server.lifecycle import start_discovery_loop, stop_discovery_loop

        client = MagicMock()
        scanned = threading.Event()

        def list_containers(**kwargs):
            scanned.set()
            return [_labeled_container()]

        client.containers.list.side_effect = list_containers
        mock_docker.from_env.return_value = client
        # One cycle only: the next is an hour away.
        orchestrator = DiscoveryOrchestrator(DiscoveryConfig(refresh_interval_s=3600))
        orchestrator.add_source(DockerDiscoverySource())

        loop, thread = start_discovery_loop(orchestrator)
        try:
            assert scanned.wait(5)
            deadline = time.monotonic() + 5
            while orchestrator.get_stats()["last_cycle"] is None and time.monotonic() < deadline:
                time.sleep(0.01)
            statuses = asyncio.run_coroutine_threadsafe(orchestrator.get_sources_status(), loop).result(5)
        finally:
            stop_discovery_loop(orchestrator, loop, thread)

        assert [(s["source_type"], s["is_healthy"], s["mcp_servers_count"]) for s in statuses] == [("docker", True, 1)]
        client.close.assert_called_once()
