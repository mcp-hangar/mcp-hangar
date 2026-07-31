"""Tests for init command with dependency detection."""

from unittest.mock import patch

import re

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli.services.dependency_detector import clear_cache


@pytest.fixture
def runner():
    """Create CLI runner."""
    return CliRunner()


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(output: str) -> str:
    """Strip ANSI styling before asserting on CLI output.

    Rich colourizes when it believes it has a terminal, which splits the strings
    these tests look for: ``"Step 0"`` arrives as
    ``"\x1b[1mStep \x1b[0m\x1b[1;36m0\x1b[0m"``. CI has no colour, so these
    assertions passed there and failed on developer machines -- a property of the
    harness reported as a defect in the code. Setting NO_COLOR/TERM on the runner
    does not help: the module builds its ``Console()`` at import time.
    """
    return _ANSI.sub("", output)


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

        Detection is pinned rather than read off the host, and the wizard is run
        with ``--skip-test``. Both are load-bearing, and the second one is why
        this test kept hanging in CI.

        Pinning `shutil.which` alone did not remove the host from the equation --
        it forced the *runtimes available* branch, which is precisely the branch
        that shells out. `init` Step 5 starts every configured server for real
        ("Starting each mcp_server to verify configuration"), so a mocked-present
        `npx` means a genuine `npx -y @modelcontextprotocol/...`, fetching
        packages from the network on a CI runner. The per-server 10s budget does
        not bound that: a release run left an orphaned `npm exec` process alive
        after the job was cancelled.

        It has hung three times on that path -- 3.12 at 300s (green on re-run),
        3.14 during the 1.6.3 release at 1080s, and 3.12 again on #657. Nothing
        here asserts on Step 5, so the wizard has no business reaching it.

        Worth knowing: `pytest-timeout` fired on the 3.12 runs and did **not** on
        3.14, where the job ran to four times the limit. Tracked in #652; the
        guard is not inert in general, only there.
        """
        from mcp_hangar.server.cli.main import app

        def mock_which(name):
            return f"/usr/bin/{name}" if name in ("docker", "podman", "npx") else None

        with patch("shutil.which", mock_which):
            clear_cache()

            result = runner.invoke(
                app,
                ["init", "-y", "--skip-claude", "--skip-test", "--config-path", str(tmp_path / "config.yaml")],
                catch_exceptions=False,
            )

        assert "Step 0" in plain(result.output)
        assert "Detecting available runtimes" in plain(result.output)

    def test_init_exits_when_no_runtimes(self, runner, tmp_path):
        """Should exit with error when no runtimes available."""
        from mcp_hangar.server.cli.main import app

        # Mock all runtimes as unavailable
        with patch("shutil.which", return_value=None):
            clear_cache()

            result = runner.invoke(
                app,
                ["init", "-y", "--skip-claude", "--skip-test", "--config-path", str(tmp_path / "config.yaml")],
            )

            assert result.exit_code == 1
            assert "No supported runtimes found" in plain(result.output)

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
                [
                    "init",
                    "-y",
                    "--skip-claude",
                    "--skip-test",
                    "--bundle",
                    "starter",
                    "--config-path",
                    str(tmp_path / "config.yaml"),
                ],
            )

            # Should show warning about missing providers
            assert "Skipping from bundle" in plain(result.output) or "missing dependencies" in plain(result.output)

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
                    "--skip-test",
                    "--mcp_servers",
                    "filesystem,github",
                    "--config-path",
                    str(tmp_path / "config.yaml"),
                ],
            )

            # Both are npx-based, should show skip message
            assert "requires npx" in plain(result.output) or "Skipping" in plain(result.output)
