#!/usr/bin/env python3
"""Validate MCP Hangar configuration file.

Checks:
- YAML syntax
- Required fields for providers
- Provider mode validity
- External API dependencies (warns about missing env vars)
- Tool schema validation

Usage:
    python scripts/validate_config.py config.yaml
    python scripts/validate_config.py config.yaml --check-env
    python scripts/validate_config.py config.yaml --strict
"""

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
from typing import Any

import yaml

# Provider requirements
PROVIDER_ENV_REQUIREMENTS: dict[str, list[str]] = {
    "github": ["GITHUB_TOKEN"],
    "gdrive": ["GOOGLE_APPLICATION_CREDENTIALS"],
    "slack": ["SLACK_BOT_TOKEN"],
    "brave-search": ["BRAVE_API_KEY"],
    "sentry": ["SENTRY_AUTH_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT"],
}

VALID_MODES = {"container", "subprocess", "http", "sse"}

REQUIRED_PROVIDER_FIELDS = {"mode"}

REQUIRED_CONTAINER_FIELDS = {"image"}
REQUIRED_SUBPROCESS_FIELDS = {"command"}
REQUIRED_HTTP_FIELDS = {"url"}


@dataclass
class ValidationResult:
    """Result of config validation."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(f"❌ {msg}")

    def add_warning(self, msg: str) -> None:
        self.warnings.append(f"⚠️  {msg}")

    def add_info(self, msg: str) -> None:
        self.info.append(f"ℹ️  {msg}")

    def print_report(self) -> None:
        """Print validation report."""
        if self.errors:
            print("\nErrors:")
            for e in self.errors:
                print(f"  {e}")

        if self.warnings:
            print("\nWarnings:")
            for w in self.warnings:
                print(f"  {w}")

        if self.info:
            print("\nInfo:")
            for i in self.info:
                print(f"  {i}")

        print()
        if self.is_valid:
            print("✅ Configuration is valid")
        else:
            print(f"❌ Configuration has {len(self.errors)} error(s)")


def validate_yaml_syntax(path: Path) -> tuple[dict | None, str | None]:
    """Validate YAML syntax and load config."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config, None
    except yaml.YAMLError as e:
        return None, f"Invalid YAML syntax: {e}"
    except FileNotFoundError:
        return None, f"File not found: {path}"
    except Exception as e:
        return None, f"Error reading file: {e}"


def validate_provider(name: str, config: dict[str, Any], result: ValidationResult, check_env: bool) -> None:
    """Validate a single provider configuration."""
    # Check required fields
    for required_field in REQUIRED_PROVIDER_FIELDS:
        if required_field not in config:
            result.add_error(f"Provider '{name}': missing required field '{required_field}'")

    mode = config.get("mode")
    if mode and mode not in VALID_MODES:
        result.add_error(f"Provider '{name}': invalid mode '{mode}' (valid: {', '.join(VALID_MODES)})")

    # Mode-specific validation
    if mode == "container":
        for field in REQUIRED_CONTAINER_FIELDS:
            if field not in config:
                result.add_error(f"Provider '{name}': container mode requires '{field}'")

        # Validate volumes format
        volumes = config.get("volumes", [])
        for vol in volumes:
            if not isinstance(vol, str) or ":" not in vol:
                result.add_warning(
                    f"Provider '{name}': invalid volume format '{vol}' (expected 'host:container[:mode]')"
                )

    elif mode == "subprocess":
        for subprocess_field in REQUIRED_SUBPROCESS_FIELDS:
            if subprocess_field not in config:
                result.add_error(f"Provider '{name}': subprocess mode requires '{subprocess_field}'")

        command = config.get("command")
        if command and not isinstance(command, list):
            result.add_error(f"Provider '{name}': 'command' must be a list")

    elif mode == "http":
        for field in REQUIRED_HTTP_FIELDS:
            if field not in config:
                result.add_error(f"Provider '{name}': http mode requires '{field}'")

    # Check environment variables
    if check_env and name in PROVIDER_ENV_REQUIREMENTS:
        missing_env = []
        for env_var in PROVIDER_ENV_REQUIREMENTS[name]:
            if not os.getenv(env_var):
                missing_env.append(env_var)
        if missing_env:
            result.add_warning(f"Provider '{name}': missing environment variables: {', '.join(missing_env)}")

    # Validate tools
    tools = config.get("tools", [])
    if not tools:
        result.add_info(f"Provider '{name}': no tools defined (will be discovered at runtime)")
    else:
        for i, tool in enumerate(tools):
            if not isinstance(tool, dict):
                result.add_error(f"Provider '{name}': tool[{i}] must be an object")
                continue
            if "name" not in tool:
                result.add_error(f"Provider '{name}': tool[{i}] missing 'name'")
            if "inputSchema" in tool:
                schema = tool["inputSchema"]
                if not isinstance(schema, dict):
                    result.add_error(f"Provider '{name}': tool '{tool.get('name', i)}' inputSchema must be an object")


def validate_retry_config(config: dict[str, Any], result: ValidationResult, provider_names: set[str]) -> None:
    """Validate retry configuration."""
    retry = config.get("retry", {})
    if not retry:
        return

    default_policy = retry.get("default_policy", {})
    if default_policy:
        max_attempts = default_policy.get("max_attempts")
        if max_attempts is not None and (not isinstance(max_attempts, int) or max_attempts < 1):
            result.add_error("retry.default_policy.max_attempts must be a positive integer")

        backoff = default_policy.get("backoff")
        valid_backoffs = {"exponential", "linear", "constant"}
        if backoff and backoff not in valid_backoffs:
            result.add_error(f"retry.default_policy.backoff must be one of: {', '.join(valid_backoffs)}")

    per_provider = retry.get("per_provider", {})
    for name in per_provider:
        if name not in provider_names:
            result.add_warning(f"retry.per_provider.{name}: provider not defined in config")


def validate_knowledge_base_config(config: dict[str, Any], result: ValidationResult) -> None:
    """Validate knowledge_base configuration."""
    kb = config.get("knowledge_base", {})
    if not kb:
        return

    if kb.get("enabled", False):
        dsn = kb.get("dsn")
        if not dsn:
            result.add_error("knowledge_base.dsn is required when enabled=true")
        elif not dsn.startswith("postgresql://"):
            result.add_warning("knowledge_base.dsn should start with 'postgresql://'")

        pool_size = kb.get("pool_size")
        if pool_size is not None and (not isinstance(pool_size, int) or pool_size < 1):
            result.add_error("knowledge_base.pool_size must be a positive integer")

        cache_ttl = kb.get("cache_ttl_s")
        if cache_ttl is not None and (not isinstance(cache_ttl, (int, float)) or cache_ttl < 0):
            result.add_error("knowledge_base.cache_ttl_s must be a non-negative number")

        result.add_info("Knowledge base: enabled (PostgreSQL)")


def validate_config(path: Path, check_env: bool = False, strict: bool = False) -> ValidationResult:
    """Validate complete configuration file."""
    result = ValidationResult()

    # Step 1: YAML syntax
    config, error = validate_yaml_syntax(path)
    if error:
        result.add_error(error)
        return result

    if not config:
        result.add_error("Config file is empty")
        return result

    # Step 2: Check providers section
    providers = config.get("providers", {})
    if not providers:
        if strict:
            result.add_error("No providers defined")
        else:
            result.add_warning("No providers defined")
        return result

    result.add_info(f"Found {len(providers)} provider(s)")

    # Step 3: Validate each provider
    provider_names = set(providers.keys())
    for name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            result.add_error(f"Provider '{name}': configuration must be an object")
            continue
        validate_provider(name, provider_config, result, check_env)

    # Step 4: Validate retry config
    validate_retry_config(config, result, provider_names)

    # Step 5: Validate knowledge base config
    validate_knowledge_base_config(config, result)

    # Step 6: Check for external dependencies
    external_providers = [p for p in provider_names if p in PROVIDER_ENV_REQUIREMENTS]
    if external_providers:
        result.add_info(f"External API providers: {', '.join(external_providers)}")

    local_providers = [p for p in provider_names if p not in PROVIDER_ENV_REQUIREMENTS]
    if local_providers:
        result.add_info(f"Local providers: {', '.join(local_providers)}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate MCP Hangar configuration file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s config.yaml                    # Basic validation
  %(prog)s config.yaml --check-env        # Also check environment variables
  %(prog)s config.yaml --strict           # Treat warnings as errors
  %(prog)s config.yaml --quiet            # Only show errors
        """,
    )
    parser.add_argument("config", type=Path, help="Path to config YAML file")
    parser.add_argument("--check-env", action="store_true", help="Check required environment variables")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only show errors")

    args = parser.parse_args()

    if not args.config.exists():
        print(f"❌ Config file not found: {args.config}", file=sys.stderr)
        return 1

    print(f"Validating: {args.config}")

    result = validate_config(args.config, check_env=args.check_env, strict=args.strict)

    if args.strict and result.warnings:
        # Convert warnings to errors in strict mode
        for w in result.warnings:
            result.errors.append(w.replace("⚠️ ", "❌ "))
        result.warnings = []

    if args.quiet:
        if result.errors:
            for e in result.errors:
                print(e)
    else:
        result.print_report()

    return 0 if result.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
