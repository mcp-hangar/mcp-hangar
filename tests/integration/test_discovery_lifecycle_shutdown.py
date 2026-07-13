"""Container-backed discovery lifecycle regression tests."""

import asyncio
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode
from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource
from mcp_hangar.server.lifecycle import ServerLifecycle


@pytest.mark.container
def test_podman_discovery_shutdown_closes_lifecycle_loop():
    """A labeled Podman container is discovered and its source loop shuts down cleanly."""
    socket_path = "/run/podman/podman.sock"
    if not Path(socket_path).is_socket():
        pytest.skip("Podman Docker-compatible Unix socket is unavailable on this host")

    container_id = subprocess.check_output(
        [
            "podman",
            "run",
            "--detach",
            "--label",
            "mcp.hangar.enabled=true",
            "--label",
            "mcp.hangar.name=discovery-lifecycle-test",
            "--label",
            "mcp.hangar.mode=http",
            "--label",
            "mcp.hangar.port=8080",
            "docker.io/library/busybox:1.36",
            "sleep",
            "60",
        ],
        text=True,
    ).strip()

    try:
        source = DockerDiscoverySource(mode=DiscoveryMode.ADDITIVE, socket_path=socket_path)
        orchestrator = DiscoveryOrchestrator(config=DiscoveryConfig(enabled=True))
        orchestrator.add_source(source)
        context = MagicMock()
        context.background_workers = []
        context.discovery_orchestrator = orchestrator
        lifecycle = ServerLifecycle(context)

        lifecycle.start()
        assert lifecycle._discovery_loop is not None
        discovered = asyncio.run_coroutine_threadsafe(source.discover(), lifecycle._discovery_loop).result()
        assert any(provider.name == "discovery-lifecycle-test" for provider in discovered)

        lifecycle.shutdown()
        assert lifecycle._discovery_loop is None
        assert lifecycle._discovery_thread is None
        context.shutdown.assert_called_once()
    finally:
        subprocess.run(["podman", "rm", "--force", container_id], check=False)
