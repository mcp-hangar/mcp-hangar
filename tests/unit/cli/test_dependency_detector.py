"""Tests for dependency detection."""

from unittest.mock import patch

from mcp_hangar.server.cli.services.dependency_detector import (
    clear_cache,
    DependencyStatus,
    detect_dependencies,
    get_install_instructions,
    is_provider_available,
    RuntimeInfo,
)


class TestRuntimeInfo:
    """Tests for RuntimeInfo dataclass."""

    def test_available_runtime(self):
        """Should represent available runtime."""
        info = RuntimeInfo(name="npx", path="/usr/bin/npx", available=True)
        assert info.name == "npx"
        assert info.path == "/usr/bin/npx"
        assert info.available is True

    def test_unavailable_runtime(self):
        """Should represent unavailable runtime."""
        info = RuntimeInfo(name="uvx", path=None, available=False)
        assert info.name == "uvx"
        assert info.path is None
        assert info.available is False


class TestDependencyStatus:
    """Tests for DependencyStatus."""

    def test_has_any_with_npx(self):
        """Should return True when npx is available."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", "/usr/bin/npx", True),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert status.has_any is True

    def test_has_any_with_docker(self):
        """Should return True when docker is available."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", "/usr/bin/docker", True),
            podman=RuntimeInfo("podman", None, False),
        )
        assert status.has_any is True

    def test_has_any_none_available(self):
        """Should return False when nothing is available."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert status.has_any is False

    def test_has_container_runtime_with_docker(self):
        """Should return True when docker is available."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", "/usr/bin/docker", True),
            podman=RuntimeInfo("podman", None, False),
        )
        assert status.has_container_runtime is True

    def test_has_container_runtime_with_podman(self):
        """Should return True when podman is available."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", "/usr/bin/podman", True),
        )
        assert status.has_container_runtime is True

    def test_available_runtimes(self):
        """Should list all available runtimes."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", "/usr/bin/npx", True),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", "/usr/bin/docker", True),
            podman=RuntimeInfo("podman", None, False),
        )
        assert status.available_runtimes == ["npx", "docker"]

    def test_missing_runtimes(self):
        """Should list missing runtimes."""
        status = DependencyStatus(
            npx=RuntimeInfo("npx", "/usr/bin/npx", True),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert "uvx" in status.missing_runtimes
        assert "docker/podman" in status.missing_runtimes
        assert "npx" not in status.missing_runtimes


class TestDetectDependencies:
    """Tests for detect_dependencies function."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def test_detects_available_runtime(self):
        """Should detect available runtime in PATH."""
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda name: f"/usr/bin/{name}" if name == "npx" else None

            deps = detect_dependencies()

            assert deps.npx.available is True
            assert deps.npx.path == "/usr/bin/npx"
            assert deps.uvx.available is False

    def test_caches_results(self):
        """Should cache detection results."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/npx"

            detect_dependencies()
            detect_dependencies()

            # Should only call once due to caching
            assert mock_which.call_count == 4  # 4 runtimes checked once

    def test_clear_cache_allows_redetection(self):
        """Should allow redetection after cache clear."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            detect_dependencies()

            clear_cache()
            mock_which.return_value = "/usr/bin/npx"
            detect_dependencies()

            # Should have been called again
            assert mock_which.call_count == 8  # 4 runtimes * 2 calls


class TestIsProviderAvailable:
    """Tests for is_provider_available function."""

    def test_npx_provider_with_npx_available(self):
        """Should return True for npx provider when npx is available."""
        deps = DependencyStatus(
            npx=RuntimeInfo("npx", "/usr/bin/npx", True),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert is_provider_available("npx", deps) is True

    def test_npx_provider_without_npx(self):
        """Should return False for npx provider when npx is not available."""
        deps = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert is_provider_available("npx", deps) is False

    def test_docker_provider_with_docker(self):
        """Should return True for docker provider when docker is available."""
        deps = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", "/usr/bin/docker", True),
            podman=RuntimeInfo("podman", None, False),
        )
        assert is_provider_available("docker", deps) is True

    def test_docker_provider_with_podman(self):
        """Should return True for docker provider when podman is available."""
        deps = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", "/usr/bin/podman", True),
        )
        assert is_provider_available("docker", deps) is True

    def test_binary_provider_always_available(self):
        """Should return True for binary providers regardless of deps."""
        deps = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert is_provider_available("binary", deps) is True

    def test_unknown_type_defaults_to_available(self):
        """Should return True for unknown install types."""
        deps = DependencyStatus(
            npx=RuntimeInfo("npx", None, False),
            uvx=RuntimeInfo("uvx", None, False),
            docker=RuntimeInfo("docker", None, False),
            podman=RuntimeInfo("podman", None, False),
        )
        assert is_provider_available("unknown", deps) is True


class TestGetInstallInstructions:
    """Tests for get_install_instructions function."""

    def test_returns_instructions_for_missing(self):
        """Should return instructions for missing runtimes."""
        instructions = get_install_instructions(["npx", "uvx"])

        assert "npx" in instructions
        assert "uvx" in instructions
        assert "node" in instructions["npx"].lower() or "Node" in instructions["npx"]

    def test_handles_docker_podman_combined(self):
        """Should handle docker/podman combined key."""
        instructions = get_install_instructions(["docker/podman"])

        assert "docker/podman" in instructions
        assert "docker" in instructions["docker/podman"].lower() or "podman" in instructions["docker/podman"].lower()
