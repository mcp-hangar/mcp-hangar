"""Tests for init command with dependency detection."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli.services.dependency_detector import clear_cache


@pytest.fixture
def runner():
    """Create CLI runner."""
    return CliRunner()


@pytest.fixture(autouse=True)
def clear_dep_cache():
    """Clear dependency cache before each test."""
    clear_cache()
    yield
    clear_cache()


class TestInitDependencyDetection:
    """Tests for init command dependency detection."""

    def test_init_shows_available_runtimes(self, runner, tmp_path):
        """Should show detected runtimes in Step 0.

        Detection is pinned rather than read off the host. This was the only
        test in the file running `init` against whatever the machine happens to
        have installed, and it is the only one that has ever hung in CI
        (300s pytest-timeout, 3.12, green on re-run). Pinning it costs nothing
        -- the assertion is about Step 0 being reported, not about which
        runtimes exist -- and removes the host from the equation.
        """
        from mcp_hangar.server.cli.main import app

        def mock_which(name):
            return f"/usr/bin/{name}" if name in ("docker", "podman", "npx") else None

        with patch("shutil.which", mock_which):
            clear_cache()

            result = runner.invoke(
                app,
                ["init", "-y", "--skip-claude", "--config-path", str(tmp_path / "config.yaml")],
                catch_exceptions=False,
            )

        assert "Step 0" in result.output
        assert "Detecting available runtimes" in result.output

    def test_init_exits_when_no_runtimes(self, runner, tmp_path):
        """Should exit with error when no runtimes available."""
        from mcp_hangar.server.cli.main import app

        # Mock all runtimes as unavailable
        with patch("shutil.which", return_value=None):
            clear_cache()

            result = runner.invoke(
                app,
                ["init", "-y", "--skip-claude", "--config-path", str(tmp_path / "config.yaml")],
            )

            assert result.exit_code == 1
            assert "No supported runtimes found" in result.output

    def test_init_filters_bundle_by_availability(self, runner, tmp_path):
        """Should filter bundle providers by available runtimes."""
        from mcp_hangar.server.cli.main import app

        # Mock only docker/podman available, no npx
        def mock_which(name):
            if name in ("docker", "podman"):
                return f"/usr/bin/{name}"
            return None

        with patch("shutil.which", mock_which):
            clear_cache()

            result = runner.invoke(
                app,
                ["init", "-y", "--skip-claude", "--bundle", "starter", "--config-path", str(tmp_path / "config.yaml")],
            )

            # Should show warning about missing providers
            assert "Skipping from bundle" in result.output or "missing dependencies" in result.output

    def test_init_validates_explicit_providers(self, runner, tmp_path):
        """Should validate explicitly specified providers."""
        from mcp_hangar.server.cli.main import app

        # Mock only docker available
        def mock_which(name):
            if name in ("docker", "podman"):
                return f"/usr/bin/{name}"
            return None

        with patch("shutil.which", mock_which):
            clear_cache()

            result = runner.invoke(
                app,
                [
                    "init",
                    "-y",
                    "--skip-claude",
                    "--mcp_servers",
                    "filesystem,github",
                    "--config-path",
                    str(tmp_path / "config.yaml"),
                ],
            )

            # Both are npx-based, should show skip message
            assert "requires npx" in result.output or "Skipping" in result.output
