"""Config file manager - handles MCP Hangar config file operations."""

from datetime import datetime
from pathlib import Path
import shutil

import yaml

from .dependency_detector import DependencyStatus, detect_dependencies
from .provider_registry import ProviderDefinition


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

    def add_provider(
        self,
        provider: ProviderDefinition,
        config_value: str | None = None,
        use_env: str | None = None,
    ) -> None:
        """Add a provider to the configuration.

        Args:
            provider: Provider definition.
            config_value: Configuration value (path or secret).
            use_env: Environment variable to use instead of value.
        """
        config = self.load()

        if "providers" not in config:
            config["providers"] = {}

        provider_entry = self._build_provider_entry(provider, config_value, use_env)
        config["providers"][provider.name] = provider_entry

        self.save(config)

    def remove_provider(self, name: str) -> bool:
        """Remove a provider from the configuration.

        Args:
            name: Provider name.

        Returns:
            True if provider was removed, False if not found.
        """
        config = self.load()

        if "providers" not in config or name not in config["providers"]:
            return False

        del config["providers"][name]
        self.save(config)
        return True

    def has_provider(self, name: str) -> bool:
        """Check if a provider exists in the configuration."""
        config = self.load()
        return name in config.get("providers", {})

    def list_providers(self) -> list[str]:
        """List all configured provider names."""
        config = self.load()
        return list(config.get("providers", {}).keys())

    def merge_providers(
        self,
        new_providers: list[ProviderDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> tuple[list[str], list[str], list[str]]:
        """Merge new providers with existing configuration.

        Preserves existing providers, adds new ones.
        Does not overwrite existing provider configurations.

        Args:
            new_providers: List of new providers to add.
            configs: Dictionary mapping provider names to their configurations.
            deps: Optional dependency status for runtime selection.

        Returns:
            Tuple of (added, skipped_existing, total) provider names.
        """
        if deps is None:
            deps = detect_dependencies()

        existing_config = self.load()

        if "providers" not in existing_config:
            existing_config["providers"] = {}

        existing_names = set(existing_config["providers"].keys())
        added = []
        skipped = []

        for provider in new_providers:
            if provider.name in existing_names:
                skipped.append(provider.name)
                continue

            config = configs.get(provider.name, {})
            provider_entry = self._build_provider_entry(
                provider,
                config.get("path") or config.get("value"),
                config.get("use_env"),
                deps,
            )
            existing_config["providers"][provider.name] = provider_entry
            added.append(provider.name)

        self.save(existing_config)
        total = list(existing_config["providers"].keys())
        return added, skipped, total

    def _build_provider_entry(
        self,
        provider: ProviderDefinition,
        config_value: str | None,
        use_env: str | None,
        deps: DependencyStatus | None = None,
    ) -> dict:
        """Build a provider configuration entry.

        Uses the preferred runtime (uvx > npx) based on available dependencies.
        """
        if deps is None:
            deps = detect_dependencies()

        entry: dict = {
            "mode": "subprocess",
            "idle_ttl_s": 300,
        }

        # Get preferred runtime and package
        runtime = provider.get_preferred_runtime(deps)
        package = provider.get_command_package(deps)

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
        if provider.config_type == "path" and config_value:
            entry["args"] = [config_value]

        # Add environment variables
        if use_env:
            entry["env"] = {use_env: f"${{{use_env}}}"}
        elif config_value and provider.env_var and provider.config_type == "secret":
            entry["env"] = {provider.env_var: config_value}

        return entry

    def generate_initial_config(
        self,
        providers: list[ProviderDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> str:
        """Generate initial config.yaml content.

        Args:
            providers: List of providers to configure.
            configs: Dictionary mapping provider names to their configurations.
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
            "providers:",
        ]

        for provider in providers:
            config = configs.get(provider.name, {})
            runtime = provider.get_preferred_runtime(deps)
            package = provider.get_command_package(deps)

            lines.append(f"  {provider.name}:")
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
        providers: list[ProviderDefinition],
        configs: dict[str, dict],
        deps: DependencyStatus | None = None,
    ) -> None:
        """Write initial configuration to file.

        Args:
            providers: List of providers to configure.
            configs: Dictionary mapping provider names to their configurations.
            deps: Optional dependency status for runtime selection.
        """
        content = self.generate_initial_config(providers, configs, deps)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            f.write(content)
