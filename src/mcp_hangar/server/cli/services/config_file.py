"""Config file manager - handles MCP Hangar config file operations."""

from datetime import datetime
from pathlib import Path
import shutil

import yaml

from .dependency_detector import DependencyStatus, detect_dependencies
from .mcp_server_registry import McpServerDefinition


class ConfigFileManager:
    """Manages MCP Hangar configuration files."""

    DEFAULT_CONFIG_DIR = Path.home() / ".config" / "mcp-hangar"
    DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"

    def __init__(self, config_path: Path | None = None):
        """Initialize with optional custom config path."""
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH

    def exists(self) -> bool:
        """Check if config file exists."""
        return self.config_path.exists()

    def load(self) -> dict:
        """Load configuration from file.

        Returns:
            Configuration dictionary, or empty dict if file doesn't exist.
        """
        if not self.config_path.exists():
            return {}

        with open(self.config_path) as f:
            return yaml.safe_load(f) or {}

    def save(self, config: dict) -> None:
        """Save configuration to file.

        Args:
            config: Configuration dictionary to save.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    def backup(self) -> Path | None:
        """Create a timestamped backup of the config file.

        Returns:
            Path to backup file, or None if file doesn't exist.
        """
        if not self.config_path.exists():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.config_path.with_suffix(f".backup.{timestamp}.yaml")
        shutil.copy2(self.config_path, backup_path)
        return backup_path

    def add_mcp_server(
        self,
        mcp_server: McpServerDefinition,
        config_value: str | None = None,
        use_env: str | None = None,
    ) -> None:
        """Add a mcp_server to the configuration.

        Args:
            mcp_server: McpServer definition.
            config_value: Configuration value (path or secret).
            use_env: Environment variable to use instead of value.
        """
        config = self.load()

        if "mcp_servers" not in config:
            config["mcp_servers"] = {}

        mcp_server_entry = self._build_mcp_server_entry(mcp_server, config_value, use_env)
        config["mcp_servers"][mcp_server.name] = mcp_server_entry

        self.save(config)

    def add_provider(
        self,
        provider: McpServerDefinition,
        config_value: str | None = None,
        use_env: str | None = None,
    ) -> None:
        """Legacy alias for add_mcp_server."""
        self.add_mcp_server(provider, config_value, use_env)

    def remove_mcp_server(self, name: str) -> bool:
        """Remove a mcp_server from the configuration.

        Args:
            name: McpServer name.

        Returns:
            True if mcp_server was removed, False if not found.
        """
        config = self.load()

        if "mcp_servers" not in config or name not in config["mcp_servers"]:
            return False

        del config["mcp_servers"][name]
        self.save(config)
        return True

    def has_mcp_server(self, name: str) -> bool:
        """Check if a mcp_server exists in the configuration."""
        config = self.load()
        return name in config.get("mcp_servers", {})

    def list_mcp_servers(self) -> list[str]:
        """List all configured mcp_server names."""
        config = self.load()
        return list(config.get("mcp_servers", {}).keys())

    def merge_mcp_servers(
        self,
        new_mcp_servers: list[McpServerDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> tuple[list[str], list[str], list[str]]:
        """Merge new mcp_servers with existing configuration.

        Preserves existing mcp_servers, adds new ones.
        Does not overwrite existing mcp_server configurations.

        Args:
            new_mcp_servers: List of new mcp_servers to add.
            configs: Dictionary mapping mcp_server names to their configurations.
            deps: Optional dependency status for runtime selection.

        Returns:
            Tuple of (added, skipped_existing, total) mcp_server names.
        """
        if deps is None:
            deps = detect_dependencies()

        existing_config = self.load()

        if "mcp_servers" not in existing_config:
            existing_config["mcp_servers"] = {}

        existing_names = set(existing_config["mcp_servers"].keys())
        added = []
        skipped = []

        for mcp_server in new_mcp_servers:
            if mcp_server.name in existing_names:
                skipped.append(mcp_server.name)
                continue

            config = configs.get(mcp_server.name, {})
            mcp_server_entry = self._build_mcp_server_entry(
                mcp_server,
                config.get("path") or config.get("value"),
                config.get("use_env"),
                deps,
            )
            existing_config["mcp_servers"][mcp_server.name] = mcp_server_entry
            added.append(mcp_server.name)

        self.save(existing_config)
        total = list(existing_config["mcp_servers"].keys())
        return added, skipped, total

    def merge_providers(
        self,
        new_providers: list[McpServerDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> tuple[list[str], list[str], list[str]]:
        """Legacy alias for merge_mcp_servers."""
        return self.merge_mcp_servers(new_providers, configs, deps)

    def _build_mcp_server_entry(
        self,
        mcp_server: McpServerDefinition,
        config_value: str | None,
        use_env: str | None,
        deps: DependencyStatus | None = None,
    ) -> dict:
        """Build a mcp_server configuration entry.

        Uses the preferred runtime (uvx > npx) based on available dependencies.
        """
        if deps is None:
            deps = detect_dependencies()

        entry: dict = {
            "mode": "subprocess",
            "idle_ttl_s": 300,
        }

        # Get preferred runtime and package
        runtime = mcp_server.get_preferred_runtime(deps)
        package = mcp_server.get_command_package(deps)

        # Build command based on runtime
        if runtime == "uvx":
            entry["command"] = ["uvx", package]
        elif runtime == "npx":
            entry["command"] = ["npx", "-y", package]
        elif runtime == "docker":
            entry["command"] = ["docker", "run", "--rm", "-i", package]
        else:
            entry["command"] = [package]

        # Add args for path-based config
        if mcp_server.config_type == "path" and config_value:
            entry["args"] = [config_value]

        # Add environment variables
        if use_env:
            entry["env"] = {use_env: f"${{{use_env}}}"}
        elif config_value and mcp_server.env_var and mcp_server.config_type == "secret":
            entry["env"] = {mcp_server.env_var: config_value}

        return entry

    def generate_initial_config(
        self,
        mcp_servers: list[McpServerDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> str:
        """Generate initial config.yaml content.

        Args:
            mcp_servers: List of mcp_servers to configure.
            configs: Dictionary mapping mcp_server names to their configurations.
            deps: Optional dependency status for runtime selection.

        Returns:
            YAML configuration string.
        """
        if deps is None:
            deps = detect_dependencies()

        lines = [
            "# MCP Hangar Configuration",
            "# Generated by 'mcp-hangar init'",
            "#",
            "# Documentation: https://docs.mcp-hangar.io/configuration",
            "",
            "mcp_servers:",
        ]

        for mcp_server in mcp_servers:
            config = configs.get(mcp_server.name, {})
            runtime = mcp_server.get_preferred_runtime(deps)
            package = mcp_server.get_command_package(deps)

            lines.append(f"  {mcp_server.name}:")
            lines.append("    mode: subprocess")

            # Generate command based on runtime
            if runtime == "uvx":
                lines.append(f'    command: [uvx, "{package}"]')
            else:
                lines.append(f'    command: [npx, -y, "{package}"]')

            if config.get("path"):
                lines.append(f'    args: ["{config["path"]}"]')

            if config.get("use_env"):
                lines.append("    env:")
                lines.append(f"      {config['use_env']}: ${{{config['use_env']}}}")
            elif config.get("value") and config.get("env_var"):
                lines.append("    env:")
                lines.append(f"      {config['env_var']}: ${{{config['env_var']}}}")

            lines.append("    idle_ttl_s: 300")
            lines.append("")

        lines.extend(
            [
                "# Health monitoring",
                "health_check:",
                "  enabled: true",
                "  interval_s: 30",
                "",
                "# Logging",
                "logging:",
                "  level: INFO",
                "  json_format: false",
            ]
        )

        return "\n".join(lines)

    def write_initial_config(
        self,
        mcp_servers: list[McpServerDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> None:
        """Write initial configuration to file.

        Args:
            mcp_servers: List of mcp_servers to configure.
            configs: Dictionary mapping mcp_server names to their configurations.
            deps: Optional dependency status for runtime selection.
        """
        content = self.generate_initial_config(mcp_servers, configs, deps)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            f.write(content)
