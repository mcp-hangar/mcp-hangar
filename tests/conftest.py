"""Pytest configuration and fixtures for MCP Hangar tests.

Provides:
- Custom markers for test categorization
- Shared fixtures for common test scenarios
- Testcontainers integration for integration tests
"""

from collections.abc import Generator
import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (fast, no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests with external services")
    config.addinivalue_line("markers", "container: Tests requiring Docker/Podman containers")
    config.addinivalue_line("markers", "langfuse: Tests requiring Langfuse container")
    config.addinivalue_line("markers", "postgres: Tests requiring PostgreSQL container")
    config.addinivalue_line("markers", "redis: Tests requiring Redis container")
    config.addinivalue_line("markers", "prometheus: Tests requiring Prometheus container")
    config.addinivalue_line("markers", "slow: Tests that take >5 seconds")
    config.addinivalue_line("markers", "stress: Stress/load tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Automatically mark tests based on their location."""
    for item in items:
        # Auto-mark tests in integration/ directory
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)

        # Auto-mark tests in unit/ directory
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)

        # Auto-mark container tests
        if "containers" in str(item.fspath):
            item.add_marker(pytest.mark.container)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options."""
    parser.addoption(
        "--run-containers",
        action="store_true",
        default=False,
        help="Run tests that require containers (Docker/Podman)",
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests",
    )
    parser.addoption(
        "--container-runtime",
        action="store",
        default="auto",
        choices=["docker", "podman", "auto"],
        help="Container runtime to use (default: auto-detect)",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip tests based on markers and options."""
    # Skip container tests unless --run-containers is passed
    if "container" in [marker.name for marker in item.iter_markers()]:
        if not item.config.getoption("--run-containers"):
            pytest.skip("Container tests skipped. Use --run-containers to run.")

    # Skip slow tests unless --run-slow is passed
    if "slow" in [marker.name for marker in item.iter_markers()]:
        if not item.config.getoption("--run-slow"):
            pytest.skip("Slow tests skipped. Use --run-slow to run.")


# ============================================================================
# Shared Fixtures
# ============================================================================


@pytest.fixture
def temp_config_dir(tmp_path) -> Generator[str, None, None]:
    """Create a temporary directory for test configurations."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    yield str(config_dir)


@pytest.fixture
def mock_env(monkeypatch) -> Generator[dict, None, None]:
    """Provide a clean environment for tests."""
    env = {}

    def setenv(key: str, value: str) -> None:
        env[key] = value
        monkeypatch.setenv(key, value)

    def delenv(key: str) -> None:
        env.pop(key, None)
        monkeypatch.delenv(key, raising=False)

    # Clear MCP-related env vars
    for key in list(os.environ.keys()):
        if key.startswith(("MCP_", "HANGAR_", "LANGFUSE_", "OTEL_")):
            monkeypatch.delenv(key, raising=False)

    env["setenv"] = setenv
    env["delenv"] = delenv

    yield env


@pytest.fixture
def sample_provider_config() -> dict:
    """Sample provider configuration for tests."""
    return {
        "name": "test-provider",
        "mode": "subprocess",
        "command": ["python", "-m", "examples.provider_math.server"],
        "idle_ttl_s": 60,
        "health_check_interval_s": 30,
        "max_consecutive_failures": 3,
    }


@pytest.fixture
def sample_tool_schema() -> dict:
    """Sample tool schema for tests."""
    return {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    }
