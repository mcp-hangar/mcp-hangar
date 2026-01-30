"""Configuration loading and provider registration.

Uses ApplicationContext for dependency injection (DIP).

Note: This module uses PROVIDERS and GROUPS from state.py for backward
compatibility. The config is loaded during startup before context is
fully initialized, so we populate the global collections which are then
shared with ApplicationContext.
"""

import os
from pathlib import Path
import re
from typing import Any

import yaml

from ..domain.model import LoadBalancerStrategy, Provider, ProviderGroup
from ..domain.security.input_validator import validate_provider_id
from ..logging_config import get_logger

# Backward compatibility - config populates these collections
# which are then shared with ApplicationContext
from .state import get_group_rebalance_saga, GROUPS, PROVIDERS

logger = get_logger(__name__)


# Environment variable pattern: ${VAR_NAME} or ${VAR_NAME:-default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively interpolate environment variables in configuration values.

    Supports patterns:
    - ${VAR_NAME} - Replace with environment variable value
    - ${VAR_NAME:-default} - Replace with value or default if not set

    Args:
        config: Configuration dictionary with potential env var references.

    Returns:
        New dictionary with environment variables interpolated.
    """

    def interpolate_value(value: Any) -> Any:
        if isinstance(value, str):

            def replace_env_var(match: re.Match) -> str:
                var_name = match.group(1)
                default = match.group(2)
                env_value = os.environ.get(var_name)
                if env_value is not None:
                    return env_value
                if default is not None:
                    return default
                logger.warning(
                    "env_var_not_found",
                    var_name=var_name,
                    using_empty=True,
                )
                return ""

            return _ENV_VAR_PATTERN.sub(replace_env_var, value)
        elif isinstance(value, dict):
            return {k: interpolate_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [interpolate_value(item) for item in value]
        return value

    return interpolate_value(config)


def load_config_from_file(config_path: str) -> dict[str, Any]:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    if not config or "providers" not in config:
        raise ValueError(f"Invalid configuration: missing 'providers' section in {config_path}")

    return config


def load_config(config: dict[str, Any]) -> None:
    """
    Load provider and group configuration.

    Creates Provider aggregates and ProviderGroup aggregates based on mode.

    Args:
        config: Dictionary mapping provider IDs to provider spec dictionaries
    """
    for provider_id, spec_dict in config.items():
        result = validate_provider_id(provider_id)
        if not result.valid:
            logger.warning("skipping_invalid_provider_id", provider_id=provider_id)
            continue

        mode = spec_dict.get("mode", "subprocess")

        if mode == "group":
            _load_group_config(provider_id, spec_dict)
            continue

        _load_provider_config(provider_id, spec_dict)


def _parse_strategy(strategy_str: str, group_id: str) -> LoadBalancerStrategy:
    """Parse load balancer strategy string."""
    try:
        return LoadBalancerStrategy(strategy_str)
    except ValueError:
        logger.warning(
            "unknown_strategy_using_default",
            strategy=strategy_str,
            group_id=group_id,
            default="round_robin",
        )
        return LoadBalancerStrategy.ROUND_ROBIN


def _load_group_members(
    group: ProviderGroup,
    group_id: str,
    members: list[dict[str, Any]],
) -> None:
    """Load group members from configuration."""
    saga = get_group_rebalance_saga()

    for member_spec in members:
        member_id = member_spec.get("id")
        if not member_id:
            logger.warning("skipping_group_member_without_id", group_id=group_id)
            continue

        result = validate_provider_id(member_id)
        if not result.valid:
            logger.warning("skipping_invalid_member_id", member_id=member_id)
            continue

        member_provider = _load_provider_config(member_id, member_spec)
        group.add_member(
            member_provider,
            weight=member_spec.get("weight", 1),
            priority=member_spec.get("priority", 1),
        )

        if saga:
            saga.register_member(member_id, group_id)


def _load_provider_config(provider_id: str, spec_dict: dict[str, Any]) -> Provider:
    """Load a single provider configuration."""
    user = spec_dict.get("user")
    if user == "current":
        user = f"{os.getuid()}:{os.getgid()}"

    tools_config = spec_dict.get("tools")
    tools = None
    if tools_config:
        tools = []
        for tool_spec in tools_config:
            tools.append(
                {
                    "name": tool_spec.get("name"),
                    "description": tool_spec.get("description", ""),
                    "inputSchema": tool_spec.get("inputSchema", tool_spec.get("input_schema", {})),
                    "outputSchema": tool_spec.get("outputSchema", tool_spec.get("output_schema")),
                }
            )

    # Process auth configuration for remote providers
    auth_config = spec_dict.get("auth")
    if auth_config:
        # Interpolate environment variables in secrets
        auth_config = _interpolate_env_vars(auth_config)

    provider = Provider(
        provider_id=provider_id,
        mode=spec_dict.get("mode", "subprocess"),
        command=spec_dict.get("command"),
        image=spec_dict.get("image"),
        endpoint=spec_dict.get("endpoint"),
        env=spec_dict.get("env", {}),
        idle_ttl_s=spec_dict.get("idle_ttl_s", 300),
        health_check_interval_s=spec_dict.get("health_check_interval_s", 60),
        max_consecutive_failures=spec_dict.get("max_consecutive_failures", 3),
        volumes=spec_dict.get("volumes", []),
        build=spec_dict.get("build"),
        resources=spec_dict.get("resources", {"memory": "512m", "cpu": "1.0"}),
        network=spec_dict.get("network") or spec_dict.get("network_mode", "none"),
        read_only=spec_dict.get("read_only", True),
        user=user,
        description=spec_dict.get("description"),
        tools=tools,
        # HTTP transport configuration
        auth=auth_config,
        tls=spec_dict.get("tls"),
        http=spec_dict.get("http"),
    )
    PROVIDERS[provider_id] = provider
    logger.debug(
        "provider_loaded",
        provider_id=provider_id,
        mode=spec_dict.get("mode", "subprocess"),
    )
    return provider


def _load_group_config(group_id: str, spec_dict: dict[str, Any]) -> None:
    """Load a provider group configuration."""
    strategy = _parse_strategy(spec_dict.get("strategy", "round_robin"), group_id)
    health_config = spec_dict.get("health", {})
    circuit_config = spec_dict.get("circuit_breaker", {})

    group = ProviderGroup(
        group_id=group_id,
        strategy=strategy,
        min_healthy=spec_dict.get("min_healthy", 1),
        auto_start=spec_dict.get("auto_start", True),
        unhealthy_threshold=health_config.get("unhealthy_threshold", 2),
        healthy_threshold=health_config.get("healthy_threshold", 1),
        circuit_failure_threshold=circuit_config.get("failure_threshold", 10),
        circuit_reset_timeout_s=circuit_config.get("reset_timeout_s", 60.0),
        description=spec_dict.get("description"),
    )

    _load_group_members(group, group_id, spec_dict.get("members", []))

    GROUPS[group_id] = group
    logger.info(
        "group_loaded",
        group_id=group_id,
        member_count=group.total_count,
        strategy=strategy.value,
    )


def load_configuration(config_path: str | None = None) -> dict[str, Any]:
    """Load provider configuration from file or use defaults.

    Returns:
        Full configuration dictionary
    """
    if config_path is None:
        config_path = os.getenv("MCP_CONFIG", "config.yaml")

    if Path(config_path).exists():
        logger.info("loading_config_from_file", config_path=config_path)
        full_config = load_config_from_file(config_path)
        load_config(full_config.get("providers", {}))
        return full_config
    else:
        logger.info("config_not_found_using_default", config_path=config_path)
        default_config = {
            "math_subprocess": {
                "mode": "subprocess",
                "command": ["python", "-m", "examples.provider_math.server"],
                "idle_ttl_s": 180,
            },
        }
        load_config(default_config)
        return {"providers": default_config}
