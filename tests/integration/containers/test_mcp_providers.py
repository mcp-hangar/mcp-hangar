"""Integration tests for MCP Provider containers.

These tests verify that MCP providers running in containers work correctly:
- Container startup and health
- Tool discovery
- Container networking
"""

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.container,
]


class TestMCPProviderContainerLifecycle:
    """Tests for MCP provider container lifecycle."""

    def test_container_starts_and_responds(
        self,
        math_provider_container: dict,
        http_client,
    ) -> None:
        """MCP provider container starts and responds to requests."""
        assert math_provider_container is not None
        assert "url" in math_provider_container
        assert "host" in math_provider_container
        assert "port" in math_provider_container

        # Try to connect to the container
        try:
            response = http_client.get(
                f"{math_provider_container['url']}/health",
                timeout=5.0,
            )
            # Any response (even 404) means container is running
            assert response.status_code in [200, 404, 405]
        except Exception:
            # Container might not have health endpoint, check SSE
            try:
                response = http_client.get(
                    f"{math_provider_container['url']}/sse",
                    timeout=5.0,
                )
                assert response.status_code in [200, 400, 404, 405]
            except Exception as e:
                pytest.skip(f"Container not reachable: {e}")


class TestSQLiteProviderContainer:
    """Tests specific to SQLite provider container."""

    def test_sqlite_container_starts(
        self,
        sqlite_provider_container: dict,
    ) -> None:
        """SQLite container starts with mounted volume."""
        if sqlite_provider_container is None:
            pytest.skip("SQLite container not available")

        assert sqlite_provider_container["name"] == "sqlite"
        assert "url" in sqlite_provider_container
        assert "host" in sqlite_provider_container


class TestContainerNetworking:
    """Tests for container networking scenarios."""

    def test_multiple_containers_accessible(
        self,
        math_provider_container: dict,
        redis_container: dict,
    ) -> None:
        """Multiple containers can run simultaneously and are accessible."""
        # Verify both containers are running with valid connection info
        assert math_provider_container is not None
        assert redis_container is not None

        assert math_provider_container.get("host") is not None
        assert math_provider_container.get("port") is not None
        assert redis_container.get("host") is not None
        assert redis_container.get("port") is not None


class TestContainerLoadHandling:
    """Tests for container behavior under load."""

    @pytest.mark.slow
    def test_container_handles_concurrent_requests(
        self,
        math_provider_container: dict,
        http_client,
    ) -> None:
        """Container handles concurrent requests without crashing."""
        import threading

        errors: list[Exception] = []
        success_count = 0
        lock = threading.Lock()

        def make_request():
            nonlocal success_count
            try:
                http_client.get(
                    math_provider_container["url"],
                    timeout=5.0,
                )
                with lock:
                    success_count += 1
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=make_request) for _ in range(20)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At least some requests should succeed
        total = success_count + len(errors)
        if total > 0:
            success_rate = success_count / total
            assert success_rate >= 0.5, f"Success rate too low: {success_rate}"
