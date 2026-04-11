"""Tests for init command existing config handling."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner
import yaml

from mcp_hangar.server.cli.services.config_file import ConfigFileManager
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


class TestConfigFileManagerMerge:
    """Tests for ConfigFileManager.merge_providers."""

    def test_merge_adds_new_providers(self):
        """Should add new providers to existing config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            # Create existing config
            existing = {
                "providers": {
                    "existing1": {"mode": "subprocess", "command": ["echo", "1"]},
                }
            }
            with open(config_path, "w") as f:
                yaml.dump(existing, f)

            mgr = ConfigFileManager(config_path)

            # Mock provider definition with required methods
            mock_provider = MagicMock()
            mock_provider.name = "new_provider"
            mock_provider.install_type = "npx"
            mock_provider.package = "@test/provider"
            mock_provider.uvx_package = "test-provider"
            mock_provider.config_type = None
            mock_provider.get_preferred_runtime.return_value = "npx"
            mock_provider.get_command_package.return_value = "@test/provider"

            added, skipped, total = mgr.merge_providers([mock_provider], {})

            assert "new_provider" in added
            assert len(skipped) == 0
            assert "existing1" in total
            assert "new_provider" in total

            # Verify file
            loaded = mgr.load()
            assert "existing1" in loaded["providers"]
            assert "new_provider" in loaded["providers"]

    def test_merge_skips_existing_providers(self):
        """Should skip providers that already exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            existing = {
                "providers": {
                    "filesystem": {"mode": "subprocess", "command": ["npx", "-y", "@test/fs"]},
                }
            }
            with open(config_path, "w") as f:
                yaml.dump(existing, f)

            mgr = ConfigFileManager(config_path)

            mock_provider = MagicMock()
            mock_provider.name = "filesystem"
            mock_provider.install_type = "npx"
            mock_provider.package = "@different/package"
            mock_provider.config_type = None

            added, skipped, total = mgr.merge_providers([mock_provider], {})

            assert len(added) == 0
            assert "filesystem" in skipped
            assert len(total) == 1

            # Verify original config preserved
            loaded = mgr.load()
            assert loaded["providers"]["filesystem"]["command"] == ["npx", "-y", "@test/fs"]

    def test_merge_mixed_new_and_existing(self):
        """Should handle mix of new and existing providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            existing = {
                "providers": {
                    "existing": {"mode": "subprocess", "command": ["echo"]},
                }
            }
            with open(config_path, "w") as f:
                yaml.dump(existing, f)

            mgr = ConfigFileManager(config_path)

            mock_existing = MagicMock()
            mock_existing.name = "existing"
            mock_existing.install_type = "npx"
            mock_existing.package = "@test/existing"
            mock_existing.uvx_package = "test-existing"
            mock_existing.config_type = None
            mock_existing.get_preferred_runtime.return_value = "npx"
            mock_existing.get_command_package.return_value = "@test/existing"

            mock_new = MagicMock()
            mock_new.name = "new"
            mock_new.install_type = "npx"
            mock_new.package = "@test/new"
            mock_new.uvx_package = "test-new"
            mock_new.config_type = None
            mock_new.get_preferred_runtime.return_value = "npx"
            mock_new.get_command_package.return_value = "@test/new"

            added, skipped, total = mgr.merge_providers([mock_existing, mock_new], {})

            assert "new" in added
            assert "existing" in skipped
            assert len(total) == 2


class TestInitExistingConfig:
    """Tests for init command with existing configuration."""

    def test_non_interactive_creates_backup(self, runner):
        """Non-interactive mode should backup and overwrite."""
        from mcp_hangar.server.cli.main import app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            # Create existing config
            existing = {"providers": {"old": {"mode": "subprocess", "command": ["old"]}}}
            with open(config_path, "w") as f:
                yaml.dump(existing, f)

            runner.invoke(
                app,
                [
                    "init",
                    "-y",
                    "--skip-claude",
                    "--skip-test",
                    "--bundle",
                    "starter",
                    "--config-path",
                    str(config_path),
                ],
            )

            # Check backup was created
            backups = list(Path(tmpdir).glob("config.backup.*.yaml"))
            assert len(backups) == 1

            # Check old provider was replaced
            with open(config_path) as f:
                new_config = yaml.safe_load(f)
            assert "old" not in new_config.get("providers", {})

    def test_abort_preserves_config(self, runner):
        """Abort should preserve existing configuration."""
        from mcp_hangar.server.cli.main import app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            # Create existing config
            existing = {"providers": {"preserved": {"mode": "subprocess", "command": ["kept"]}}}
            with open(config_path, "w") as f:
                yaml.dump(existing, f)

            # Mock questionary to return abort
            with patch("mcp_hangar.server.cli.commands.init.questionary") as mock_q:
                mock_q.select.return_value.ask.return_value = "abort"
                mock_q.checkbox.return_value.ask.return_value = ["filesystem"]

                runner.invoke(
                    app,
                    [
                        "init",
                        "--skip-claude",
                        "--skip-test",
                        "--bundle",
                        "starter",
                        "--config-path",
                        str(config_path),
                    ],
                )

            # Verify config preserved
            with open(config_path) as f:
                loaded = yaml.safe_load(f)
            assert "preserved" in loaded.get("providers", {})
            assert loaded["providers"]["preserved"]["command"] == ["kept"]

    def test_reset_flag_skips_existing_config_handling(self, runner):
        """--reset flag should overwrite without prompting."""
        from mcp_hangar.server.cli.main import app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            existing = {"providers": {"old": {"mode": "subprocess", "command": ["old"]}}}
            with open(config_path, "w") as f:
                yaml.dump(existing, f)

            runner.invoke(
                app,
                [
                    "init",
                    "-y",
                    "--skip-claude",
                    "--skip-test",
                    "--reset",
                    "--bundle",
                    "starter",
                    "--config-path",
                    str(config_path),
                ],
            )

            # Check no backup was created (reset flag)
            backups = list(Path(tmpdir).glob("config.backup.*.yaml"))
            assert len(backups) == 0

            # Check old provider was replaced
            with open(config_path) as f:
                new_config = yaml.safe_load(f)
            assert "old" not in new_config.get("providers", {})
