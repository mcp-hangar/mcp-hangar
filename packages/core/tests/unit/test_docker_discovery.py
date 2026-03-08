"""Unit tests for Docker discovery reconnection and backoff.

Tests DockerDiscoverySource automatic reconnection with exponential
backoff when Docker daemon connection is lost.
"""

from unittest.mock import MagicMock, patch

import pytest


# Patch docker module availability for all tests in this module
@pytest.fixture(autouse=True)
def _mock_docker_available():
    """Ensure DOCKER_AVAILABLE is True and docker module is mocked."""
    with patch("mcp_hangar.infrastructure.discovery.docker_source.DOCKER_AVAILABLE", True):
        with patch("mcp_hangar.infrastructure.discovery.docker_source.docker") as mock_docker:
            mock_docker.DockerClient = MagicMock
            mock_docker.from_env.return_value = MagicMock()
            yield mock_docker


@pytest.fixture
def mock_docker(_mock_docker_available):
    """Provide the mocked docker module."""
    return _mock_docker_available


class TestEnsureClientRetry:
    """Tests for _ensure_client() retry with exponential backoff."""

    def test_ensure_client_retries_on_failure(self, mock_docker):
        """_ensure_client() retries connection up to max_retries on failure."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.DockerClient.return_value = mock_client
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=3, initial_backoff_s=0.01)

        with patch("time.sleep"):
            with pytest.raises(DockerException, match="Failed to connect.*3 attempts"):
                source._ensure_client()

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

        with patch("time.sleep"):
            source._ensure_client()

        assert source._client is ok_client

    def test_ensure_client_raises_after_max_retries(self, mock_docker):
        """_ensure_client() raises after exhausting max_retries."""
        from docker.errors import DockerException

        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=2, initial_backoff_s=0.01)

        with patch("time.sleep"):
            with pytest.raises(DockerException, match="Failed to connect.*2 attempts"):
                source._ensure_client()

    def test_ensure_client_skips_when_already_connected(self, mock_docker):
        """_ensure_client() is a no-op when client is already connected."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource()
        existing_client = MagicMock()
        source._client = existing_client

        source._ensure_client()

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
        with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            with pytest.raises(DockerException):
                source._ensure_client()

        # Should have (max_retries - 1) sleeps = 4
        assert len(sleep_calls) == 4

        # Verify exponential increase (with jitter tolerance)
        # Expected base delays: 1, 2, 4, 8
        for i, actual in enumerate(sleep_calls):
            expected_base = 1.0 * (2**i)
            # Allow 10% jitter
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
        with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            with pytest.raises(DockerException):
                source._ensure_client()

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

        with patch("time.sleep"):
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

        with patch("time.sleep"):
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

        with patch("time.sleep"):
            result1 = await source.discover()

        assert result1 == []

        # Second call: reconnect and succeed
        mock_docker.from_env.side_effect = [ok_client]
        with patch("time.sleep"):
            result2 = await source.discover()

        assert len(result2) == 1
        assert result2[0].name == "test-provider"


class TestReconnect:
    """Tests for _reconnect() method."""

    def test_reconnect_closes_old_client(self, mock_docker):
        """_reconnect() closes old client before creating new one."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        old_client = MagicMock()
        new_client = MagicMock()
        new_client.ping.return_value = True

        mock_docker.from_env.return_value = new_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)
        source._client = old_client

        source._reconnect()

        old_client.close.assert_called_once()
        assert source._client is new_client

    def test_reconnect_handles_close_error(self, mock_docker):
        """_reconnect() handles error during old client close gracefully."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        old_client = MagicMock()
        old_client.close.side_effect = RuntimeError("Socket already closed")

        new_client = MagicMock()
        new_client.ping.return_value = True
        mock_docker.from_env.return_value = new_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)
        source._client = old_client

        # Should not raise despite close() error
        source._reconnect()

        assert source._client is new_client

    def test_reconnect_with_no_existing_client(self, mock_docker):
        """_reconnect() works when no existing client."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        new_client = MagicMock()
        new_client.ping.return_value = True
        mock_docker.from_env.return_value = new_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)
        assert source._client is None

        source._reconnect()

        assert source._client is new_client


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
        """health_check() returns True when Docker daemon is accessible."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_docker.from_env.return_value = mock_client

        source = DockerDiscoverySource(max_retries=1, initial_backoff_s=0.01)

        result = await source.health_check()

        assert result is True


class TestInitConfiguration:
    """Tests for __init__ reconnection configuration."""

    def test_default_reconnection_params(self, mock_docker):
        """Default reconnection parameters are set correctly."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource()

        assert source._max_retries == 5
        assert source._initial_backoff_s == 1.0
        assert source._max_backoff_s == 30.0
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
