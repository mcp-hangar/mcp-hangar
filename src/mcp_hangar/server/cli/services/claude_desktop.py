"""Claude Desktop manager - handles Claude Desktop config detection and updates."""

from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil


class ClaudeDesktopManager:
    """Manages Claude Desktop configuration integration."""

    # Platform-specific paths for Claude Desktop config
    PLATFORM_PATHS = {
        "Darwin": Path.home() / "Library" / "Application Support" / "Claude",
        "Linux": Path.home() / ".config" / "claude",
        "Windows": Path(os.environ.get("APPDATA", "")) / "Claude",
    }

    # Fallback search paths
    FALLBACK_PATHS = [
        Path.home() / ".claude",
        Path.home() / ".config" / "claude",
    ]

    def __init__(self, config_path: Path | None = None):
        """Initialize with optional explicit config path."""
        self._explicit_path = config_path
        self._detected_path: Path | None = None
        self._config: dict | None = None

    @property
    def config_path(self) -> Path | None:
        """Get the Claude Desktop config file path."""
        if self._explicit_path:
            return self._explicit_path
        if self._detected_path is None:
            self._detected_path, self._config = self._detect()
        return self._detected_path

    @property
    def config(self) -> dict | None:
        """Get the loaded Claude Desktop configuration."""
        if self._config is None and self.config_path:
            self._config = self._load(self.config_path)
        return self._config

    def exists(self) -> bool:
        """Check if Claude Desktop config exists."""
        return self.config_path is not None and self.config_path.exists()

    def get_mcp_servers(self) -> dict:
        """Get existing MCP servers from config."""
        if self.config:
            return self.config.get("mcpServers", {})
        return {}

    def backup(self) -> Path | None:
        """Create a timestamped backup of the config file.

        Returns:
            Path to backup file, or None if file doesn't exist.
        """
        if not self.config_path or not self.config_path.exists():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.config_path.with_suffix(f".backup.{timestamp}.json")
        shutil.copy2(self.config_path, backup_path)
        return backup_path

    def update_for_hangar(self, hangar_config_path: Path) -> None:
        """Update Claude Desktop config to use MCP Hangar.

        Args:
            hangar_config_path: Path to the MCP Hangar config file.
        """
        if not self.config_path:
            raise ValueError("Claude Desktop config path not set")

        new_config = self.config.copy() if self.config else {}
        new_config["mcpServers"] = self._generate_hangar_entry(hangar_config_path)

        with open(self.config_path, "w") as f:
            json.dump(new_config, f, indent=2)

        self._config = new_config

    def _detect(self) -> tuple[Path | None, dict | None]:
        """Detect Claude Desktop installation and load config.

        Returns:
            Tuple of (config_path, config) or (None, None) if not found.
        """
        search_paths = []

        # Add platform-specific path
        system = platform.system()
        if system in self.PLATFORM_PATHS:
            search_paths.append(self.PLATFORM_PATHS[system])

        # Add fallback paths
        search_paths.extend(self.FALLBACK_PATHS)

        for base_path in search_paths:
            config_file = base_path / "claude_desktop_config.json"
            if config_file.exists():
                config = self._load(config_file)
                if config is not None:
                    return config_file, config

        return None, None

    def _load(self, path: Path) -> dict | None:
        """Load config from file."""
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _generate_hangar_entry(self, hangar_config_path: Path) -> dict:
        """Generate MCP server entry for MCP Hangar."""
        return {
            "mcp-hangar": {
                "command": "mcp-hangar",
                "args": ["serve", "--config", str(hangar_config_path)],
            }
        }
