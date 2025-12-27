"""Registry server with FastMCP integration, CQRS, security hardening, and structured logging."""

import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
import yaml

from .application.commands import register_all_handlers as register_command_handlers
from .application.event_handlers import AlertEventHandler, AuditEventHandler, LoggingEventHandler, MetricsEventHandler
from .application.mcp.tooling import (
    chain_validators,
    key_global,
    key_registry_invoke,
    mcp_tool_wrapper,
    ToolErrorPayload,
)
from .application.queries import register_all_handlers as register_query_handlers
from .application.sagas import GroupRebalanceSaga
from .bootstrap.runtime import create_runtime
from .domain.exceptions import RateLimitExceeded
from .domain.model import LoadBalancerStrategy, Provider, ProviderGroup
from .domain.security.input_validator import (
    validate_arguments,
    validate_provider_id,
    validate_timeout,
    validate_tool_name,
)
from .domain.security.sanitizer import sanitize_log_message
from .gc import BackgroundWorker
from .application.commands import InvokeToolCommand, StartProviderCommand, StopProviderCommand
from .infrastructure.query_bus import GetProviderQuery, GetProviderToolsQuery, ListProvidersQuery
from .infrastructure.saga_manager import get_saga_manager


# Structured JSON logging to stderr
class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record):
        log_obj = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": sanitize_log_message(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def setup_logging(level=logging.INFO, log_file: Optional[str] = None):
    """Set up structured JSON logging to stderr and optionally to file."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()  # Remove any existing handlers
    root_logger.setLevel(level)

    # Always log to stderr (for Claude Desktop)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(stderr_handler)

    # Optionally log to file
    if log_file:
        try:
            # Ensure directory exists
            log_dir = Path(log_file).parent
            log_dir.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            file_handler.setFormatter(JSONFormatter())
            root_logger.addHandler(file_handler)
            root_logger.info(f"Logging to file: {log_file}")
        except Exception as e:
            root_logger.warning(f"Could not setup file logging: {e}")


# Initialize MCP server
mcp = FastMCP("mcp-registry")

# Runtime wiring (no module-level singletons)
_RUNTIME = create_runtime()

# Convenience bindings used throughout this module
PROVIDER_REPOSITORY = _RUNTIME.repository
EVENT_BUS = _RUNTIME.event_bus
COMMAND_BUS = _RUNTIME.command_bus
QUERY_BUS = _RUNTIME.query_bus
RATE_LIMIT_CONFIG = _RUNTIME.rate_limit_config
RATE_LIMITER = _RUNTIME.rate_limiter
INPUT_VALIDATOR = _RUNTIME.input_validator
SECURITY_HANDLER = _RUNTIME.security_handler


# Backward compatibility: PROVIDERS dict backed by repository
class ProviderDict:
    """Dictionary-like wrapper around provider repository for backward compatibility."""

    def __init__(self, repository):
        self._repo = repository

    def __getitem__(self, key):
        provider = self._repo.get(key)
        if provider is None:
            raise KeyError(key)
        return provider

    def __setitem__(self, key, value):
        self._repo.add(key, value)

    def __contains__(self, key):
        return self._repo.exists(key)

    def get(self, key, default=None):
        return self._repo.get(key) or default

    def items(self):
        return self._repo.get_all().items()

    def keys(self):
        return self._repo.get_all_ids()

    def values(self):
        return self._repo.get_all().values()


PROVIDERS = ProviderDict(PROVIDER_REPOSITORY)


# Provider Groups storage - simple dict wrapper
GROUPS: Dict[str, ProviderGroup] = {}

# Rebalance saga instance (will be initialized in main())
_GROUP_REBALANCE_SAGA: Optional[GroupRebalanceSaga] = None


def _check_rate_limit(key: str = "global") -> None:
    """Check rate limit and raise exception if exceeded."""
    result = RATE_LIMITER.consume(key)
    if not result.allowed:
        SECURITY_HANDLER.log_rate_limit_exceeded(
            limit=result.limit,
            window_seconds=int(1.0 / RATE_LIMIT_CONFIG.requests_per_second),
        )
        raise RateLimitExceeded(
            limit=result.limit,
            window_seconds=int(1.0 / RATE_LIMIT_CONFIG.requests_per_second),
        )


def _tool_error_mapper(exc: Exception) -> ToolErrorPayload:
    """Map exceptions to a stable MCP tool error payload."""
    # Keep payload minimal and stable for clients; preserve type for debugging.
    return ToolErrorPayload(
        error=str(exc) or "unknown error",
        error_type=type(exc).__name__,
        details={},
    )


def _tool_error_hook(exc: Exception, context: dict) -> None:
    """Best-effort hook for logging/security telemetry on tool failures.

    NOTE: SecurityEventHandler does not expose a dedicated `log_tool_error` API.
    We map tool failures onto an existing, stable API (`log_validation_failed`)
    to avoid crashing the tool execution path.
    """
    try:
        SECURITY_HANDLER.log_validation_failed(
            field="tool",
            message=f"{type(exc).__name__}: {str(exc) or 'unknown error'}",
            provider_id=context.get("provider_id"),
            value=context.get("provider_id"),
        )
    except Exception:
        # Security handler logging must never break the tool call path.
        pass


def _validate_provider_id(provider: str) -> None:
    """Validate provider ID and raise exception if invalid."""
    result = validate_provider_id(provider)
    if not result.valid:
        SECURITY_HANDLER.log_validation_failed(
            field="provider",
            message=(result.errors[0].message if result.errors else "Invalid provider ID"),
            provider_id=provider,
        )
        raise ValueError(f"invalid_provider_id: {result.errors[0].message if result.errors else 'validation failed'}")


def _validate_tool_name_input(tool: str) -> None:
    """Validate tool name and raise exception if invalid."""
    result = validate_tool_name(tool)
    if not result.valid:
        SECURITY_HANDLER.log_validation_failed(
            field="tool",
            message=result.errors[0].message if result.errors else "Invalid tool name",
        )
        raise ValueError(f"invalid_tool_name: {result.errors[0].message if result.errors else 'validation failed'}")


def _validate_arguments_input(arguments: dict) -> None:
    """Validate tool arguments and raise exception if invalid."""
    result = validate_arguments(arguments)
    if not result.valid:
        SECURITY_HANDLER.log_validation_failed(
            field="arguments",
            message=result.errors[0].message if result.errors else "Invalid arguments",
        )
        raise ValueError(f"invalid_arguments: {result.errors[0].message if result.errors else 'validation failed'}")


def _validate_timeout_input(timeout: float) -> None:
    """Validate timeout and raise exception if invalid."""
    result = validate_timeout(timeout)
    if not result.valid:
        SECURITY_HANDLER.log_validation_failed(
            field="timeout",
            message=result.errors[0].message if result.errors else "Invalid timeout",
        )
        raise ValueError(f"invalid_timeout: {result.errors[0].message if result.errors else 'validation failed'}")


@mcp.tool(name="registry_list")
@mcp_tool_wrapper(
    tool_name="registry_list",
    rate_limit_key=key_global,
    check_rate_limit=lambda key: _check_rate_limit("registry_list"),
    validate=None,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=_tool_error_hook,
)
def registry_list(state_filter: Optional[str] = None) -> dict:
    """
    List all providers and groups with status and metadata.

    Args:
        state_filter: Optional filter by state (cold, ready, degraded, dead)

    Returns:
        Dictionary with 'providers' and 'groups' keys
    """
    query = ListProvidersQuery(state_filter=state_filter)
    summaries = QUERY_BUS.execute(query)

    # Include groups in response
    groups_list = []
    for group_id, group in GROUPS.items():
        group_info = group.to_status_dict()
        # Apply state filter to groups if specified
        if state_filter and group_info.get("state") != state_filter:
            continue
        groups_list.append(group_info)

    return {
        "providers": [s.to_dict() for s in summaries],
        "groups": groups_list,
    }


@mcp.tool(name="registry_start")
@mcp_tool_wrapper(
    tool_name="registry_start",
    rate_limit_key=lambda provider: f"registry_start:{provider}",
    check_rate_limit=_check_rate_limit,
    validate=_validate_provider_id,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=lambda exc, ctx: _tool_error_hook(exc, ctx),
)
def registry_start(provider: str) -> dict:
    """
    Explicitly start a provider or all members of a group.

    Args:
        provider: Provider ID or Group ID to start

    Returns:
        Dictionary with provider/group state and tools

    Raises:
        ValueError: If provider ID is unknown or invalid
    """
    # Check if it's a group
    if provider in GROUPS:
        group = GROUPS[provider]
        started = group.start_all()
        return {
            "group": provider,
            "state": group.state.value,
            "members_started": started,
            "healthy_count": group.healthy_count,
            "total_members": group.total_count,
        }

    if provider not in PROVIDERS:
        raise ValueError(f"unknown_provider: {provider}")

    command = StartProviderCommand(provider_id=provider)
    return COMMAND_BUS.send(command)


@mcp.tool(name="registry_stop")
@mcp_tool_wrapper(
    tool_name="registry_stop",
    rate_limit_key=lambda provider: f"registry_stop:{provider}",
    check_rate_limit=_check_rate_limit,
    validate=_validate_provider_id,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=lambda exc, ctx: _tool_error_hook(exc, ctx),
)
def registry_stop(provider: str) -> dict:
    """
    Explicitly stop a provider or all members of a group.

    Args:
        provider: Provider ID or Group ID to stop

    Returns:
        Confirmation dictionary

    Raises:
        ValueError: If provider ID is unknown or invalid
    """
    # Check if it's a group
    if provider in GROUPS:
        group = GROUPS[provider]
        group.stop_all()
        return {
            "group": provider,
            "state": group.state.value,
            "stopped": True,
        }

    if provider not in PROVIDERS:
        raise ValueError(f"unknown_provider: {provider}")

    command = StopProviderCommand(provider_id=provider)
    return COMMAND_BUS.send(command)


@mcp.tool(name="registry_tools")
@mcp_tool_wrapper(
    tool_name="registry_tools",
    rate_limit_key=lambda provider: f"registry_tools:{provider}",
    check_rate_limit=_check_rate_limit,
    validate=_validate_provider_id,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=lambda exc, ctx: _tool_error_hook(exc, ctx),
)
def registry_tools(provider: str) -> dict:
    """
    Get detailed tool schemas for a provider.

    Args:
        provider: Provider ID

    Returns:
        Dictionary with provider ID and list of tool schemas

    Raises:
        ValueError: If provider ID is unknown or invalid
    """
    # Check if it's a group first
    if provider in GROUPS:
        group = GROUPS[provider]
        # Ensure group has healthy members
        selected = group.select_member()
        if not selected:
            raise ValueError(f"no_healthy_members_in_group: {provider}")
        # Use selected member's ID for tools query
        COMMAND_BUS.send(StartProviderCommand(provider_id=selected.provider_id))
        query = GetProviderToolsQuery(provider_id=selected.provider_id)
        tools = QUERY_BUS.execute(query)
        return {
            "provider": provider,
            "group": True,
            "tools": [t.to_dict() for t in tools],
        }

    if provider not in PROVIDERS:
        raise ValueError(f"unknown_provider: {provider}")

    # Ensure provider is ready first
    COMMAND_BUS.send(StartProviderCommand(provider_id=provider))

    # Then query tools
    query = GetProviderToolsQuery(provider_id=provider)
    tools = QUERY_BUS.execute(query)
    return {"provider": provider, "tools": [t.to_dict() for t in tools]}


@mcp.tool(name="registry_invoke")
@mcp_tool_wrapper(
    tool_name="registry_invoke",
    rate_limit_key=key_registry_invoke,
    check_rate_limit=_check_rate_limit,
    validate=chain_validators(
        lambda provider, tool, arguments, timeout=30.0: _validate_provider_id(provider),
        lambda provider, tool, arguments, timeout=30.0: _validate_tool_name_input(tool),
        lambda provider, tool, arguments, timeout=30.0: _validate_arguments_input(arguments),
        lambda provider, tool, arguments, timeout=30.0: _validate_timeout_input(timeout),
    ),
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=lambda exc, ctx: _tool_error_hook(exc, ctx),
)
def registry_invoke(provider: str, tool: str, arguments: dict, timeout: float = 30.0) -> dict:
    """
    Invoke a tool on a provider or provider group.

    If the provider is a group, automatically selects a healthy member
    using the configured load balancing strategy. Retry on failure with
    a different member if available.

    Args:
        provider: Provider ID or Group ID
        tool: Tool name
        arguments: Tool arguments
        timeout: Timeout in seconds (default: 30.0)

    Returns:
        Tool result

    Raises:
        ValueError: If provider ID is unknown or inputs are invalid
    """
    # Check if this is a group
    if provider in GROUPS:
        return _invoke_on_group(provider, tool, arguments, timeout)

    if provider not in PROVIDERS:
        raise ValueError(f"unknown_provider: {provider}")

    command = InvokeToolCommand(
        provider_id=provider,
        tool_name=tool,
        arguments=arguments,
        timeout=timeout,
    )
    return COMMAND_BUS.send(command)


def _invoke_on_group(group_id: str, tool: str, arguments: dict, timeout: float) -> dict:
    """
    Invoke a tool on a provider group with load balancing.

    Selects a member using the group's load balancer, invokes the tool,
    and handles failures with automatic retry on a different member.
    """
    group = GROUPS[group_id]

    if not group.is_available:
        raise ValueError(f"group_not_available: {group_id} (state={group.state.value})")

    selected = group.select_member()
    if not selected:
        raise ValueError(f"no_healthy_members_in_group: {group_id}")

    return _invoke_with_retry(group, tool, arguments, timeout, max_attempts=2)


def _invoke_with_retry(group: ProviderGroup, tool: str, arguments: dict, timeout: float, max_attempts: int) -> dict:
    """Invoke tool with retry on different members."""
    first_error = None
    tried_members: set = set()

    for _ in range(max_attempts):
        selected = group.select_member()
        if not selected or selected.provider_id in tried_members:
            break

        tried_members.add(selected.provider_id)

        try:
            command = InvokeToolCommand(
                provider_id=selected.provider_id,
                tool_name=tool,
                arguments=arguments,
                timeout=timeout,
            )
            result = COMMAND_BUS.send(command)
            group.report_success(selected.provider_id)
            return result
        except Exception as e:
            group.report_failure(selected.provider_id)
            first_error = first_error or e

    raise first_error or ValueError("no_healthy_members_in_group")


@mcp.tool(name="registry_details")
@mcp_tool_wrapper(
    tool_name="registry_details",
    rate_limit_key=lambda provider: f"registry_details:{provider}",
    check_rate_limit=_check_rate_limit,
    validate=_validate_provider_id,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=lambda exc, ctx: _tool_error_hook(exc, ctx),
)
def registry_details(provider: str) -> dict:
    """
    Get detailed information about a provider or group.

    Args:
        provider: Provider ID or Group ID

    Returns:
        Dictionary with full provider/group details

    Raises:
        ValueError: If provider ID is unknown or invalid
    """
    # Check if it's a group
    if provider in GROUPS:
        group = GROUPS[provider]
        return group.to_status_dict()

    if provider not in PROVIDERS:
        raise ValueError(f"unknown_provider: {provider}")

    query = GetProviderQuery(provider_id=provider)
    details = QUERY_BUS.execute(query)
    return details.to_dict()


@mcp.tool(name="registry_health")
@mcp_tool_wrapper(
    tool_name="registry_health",
    rate_limit_key=key_global,
    check_rate_limit=lambda key: _check_rate_limit("registry_health"),
    validate=None,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=_tool_error_hook,
)
def registry_health() -> dict:
    """
    Get registry health status including security metrics.

    Returns:
        Dictionary with health information
    """
    # Get rate limiter stats
    rate_limit_stats = RATE_LIMITER.get_stats()

    # Get provider counts by state
    providers = list(PROVIDERS.values())
    state_counts = {}
    for p in providers:
        state = str(p.state)
        state_counts[state] = state_counts.get(state, 0) + 1

    # Get group counts by state
    group_state_counts = {}
    total_group_members = 0
    healthy_group_members = 0
    for group in GROUPS.values():
        state = group.state.value
        group_state_counts[state] = group_state_counts.get(state, 0) + 1
        total_group_members += group.total_count
        healthy_group_members += group.healthy_count

    return {
        "status": "healthy",
        "providers": {
            "total": len(providers),
            "by_state": state_counts,
        },
        "groups": {
            "total": len(GROUPS),
            "by_state": group_state_counts,
            "total_members": total_group_members,
            "healthy_members": healthy_group_members,
        },
        "security": {
            "rate_limiting": rate_limit_stats,
        },
    }


# --- Group-specific tools ---


@mcp.tool(name="registry_group_list")
@mcp_tool_wrapper(
    tool_name="registry_group_list",
    rate_limit_key=key_global,
    check_rate_limit=lambda key: _check_rate_limit("registry_group_list"),
    validate=None,
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=_tool_error_hook,
)
def registry_group_list() -> dict:
    """
    List all provider groups with detailed status.

    Returns:
        Dictionary with 'groups' key containing list of group info
    """
    return {"groups": [group.to_status_dict() for group in GROUPS.values()]}


@mcp.tool(name="registry_group_rebalance")
@mcp_tool_wrapper(
    tool_name="registry_group_rebalance",
    rate_limit_key=lambda group: f"registry_group_rebalance:{group}",
    check_rate_limit=_check_rate_limit,
    validate=_validate_provider_id,  # Same validation rules as provider IDs
    error_mapper=lambda exc: _tool_error_mapper(exc),
    on_error=lambda exc, ctx: _tool_error_hook(exc, ctx),
)
def registry_group_rebalance(group: str) -> dict:
    """
    Manually trigger rebalancing for a group.

    Re-evaluates health of all members and updates rotation.

    Args:
        group: Group ID to rebalance

    Returns:
        Dictionary with group status after rebalancing

    Raises:
        ValueError: If group ID is unknown
    """
    if group not in GROUPS:
        raise ValueError(f"unknown_group: {group}")

    g = GROUPS[group]
    g.rebalance()

    return {
        "group_id": group,
        "state": g.state.value,
        "healthy_count": g.healthy_count,
        "total_members": g.total_count,
        "members_in_rotation": [m.id for m in g.members if m.in_rotation],
    }


def load_config_from_file(config_path: str) -> Dict[str, Any]:
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

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    if not config or "providers" not in config:
        raise ValueError(f"Invalid configuration: missing 'providers' section in {config_path}")

    return config


def load_config(config: Dict[str, Any]) -> None:
    """
    Load provider and group configuration.

    Creates Provider aggregates and ProviderGroup aggregates based on mode.

    Args:
        config: Dictionary mapping provider IDs to provider spec dictionaries
    """
    logger = logging.getLogger(__name__)

    for provider_id, spec_dict in config.items():
        # Validate provider ID
        result = validate_provider_id(provider_id)
        if not result.valid:
            logger.warning(f"Skipping provider with invalid ID: {provider_id}")
            continue

        mode = spec_dict.get("mode", "subprocess")

        # Handle group mode specially
        if mode == "group":
            _load_group_config(provider_id, spec_dict, logger)
            continue

        # Regular provider
        _load_provider_config(provider_id, spec_dict, logger)


def _parse_strategy(strategy_str: str, group_id: str, logger: logging.Logger) -> LoadBalancerStrategy:
    """Parse load balancer strategy string."""
    try:
        return LoadBalancerStrategy(strategy_str)
    except ValueError:
        logger.warning(f"Unknown strategy '{strategy_str}' for group {group_id}, using round_robin")
        return LoadBalancerStrategy.ROUND_ROBIN


def _load_group_members(
    group: ProviderGroup,
    group_id: str,
    members: List[Dict[str, Any]],
    logger: logging.Logger,
) -> None:
    """Load group members from configuration."""
    global _GROUP_REBALANCE_SAGA

    for member_spec in members:
        member_id = member_spec.get("id")
        if not member_id:
            logger.warning(f"Skipping group member without 'id' in group {group_id}")
            continue

        result = validate_provider_id(member_id)
        if not result.valid:
            logger.warning(f"Skipping member with invalid ID: {member_id}")
            continue

        member_provider = _load_provider_config(member_id, member_spec, logger)
        group.add_member(
            member_provider,
            weight=member_spec.get("weight", 1),
            priority=member_spec.get("priority", 1),
        )

        if _GROUP_REBALANCE_SAGA:
            _GROUP_REBALANCE_SAGA.register_member(member_id, group_id)


def _load_provider_config(provider_id: str, spec_dict: Dict[str, Any], logger: logging.Logger) -> Provider:
    """Load a single provider configuration."""
    # Resolve user if set to "current"
    user = spec_dict.get("user")
    if user == "current":
        user = f"{os.getuid()}:{os.getgid()}"

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
        # Container-specific options
        volumes=spec_dict.get("volumes", []),
        build=spec_dict.get("build"),
        resources=spec_dict.get("resources", {"memory": "512m", "cpu": "1.0"}),
        network=spec_dict.get("network", "none"),
        read_only=spec_dict.get("read_only", True),
        user=user,
        description=spec_dict.get("description"),
    )
    PROVIDERS[provider_id] = provider
    return provider


def _load_group_config(group_id: str, spec_dict: Dict[str, Any], logger: logging.Logger) -> None:
    """Load a provider group configuration."""
    global _GROUP_REBALANCE_SAGA

    strategy = _parse_strategy(spec_dict.get("strategy", "round_robin"), group_id, logger)
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

    _load_group_members(group, group_id, spec_dict.get("members", []), logger)

    GROUPS[group_id] = group
    logger.info(f"Loaded group {group_id} with {group.total_count} members, " f"strategy={strategy.value}")


def _parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="MCP Registry Server")
    parser.add_argument("--http", action="store_true", help="Run HTTP server mode")
    parser.add_argument("--host", type=str, default=None, help="HTTP server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="HTTP server port (default: 8000)")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml file")
    return parser.parse_args()


def _ensure_data_dir(logger: logging.Logger) -> None:
    """Ensure data directory exists for persistent storage."""
    data_dir = Path("./data")
    if not data_dir.exists():
        try:
            data_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
            logger.info(f"Created data directory: {data_dir.absolute()}")
        except OSError as e:
            logger.warning(f"Could not create data directory: {e}")


def _init_event_handlers(event_bus, security_handler, logger: logging.Logger) -> None:
    """Initialize and register event handlers."""
    logging_handler = LoggingEventHandler()
    event_bus.subscribe_to_all(logging_handler.handle)

    metrics_handler = MetricsEventHandler()
    event_bus.subscribe_to_all(metrics_handler.handle)

    alert_handler = AlertEventHandler()
    event_bus.subscribe_to_all(alert_handler.handle)

    audit_handler = AuditEventHandler()
    event_bus.subscribe_to_all(audit_handler.handle)

    event_bus.subscribe_to_all(security_handler.handle)

    logger.info("event_handlers_registered: logging, metrics, alert, audit, security")


def _init_cqrs(runtime, logger: logging.Logger) -> None:
    """Initialize CQRS command and query handlers."""
    register_command_handlers(runtime.command_bus, PROVIDER_REPOSITORY, runtime.event_bus)
    register_query_handlers(runtime.query_bus, PROVIDER_REPOSITORY)
    logger.info("cqrs_handlers_registered")


def _init_saga(logger: logging.Logger) -> None:
    """Initialize group rebalance saga."""
    global _GROUP_REBALANCE_SAGA
    _GROUP_REBALANCE_SAGA = GroupRebalanceSaga(groups=GROUPS)
    saga_manager = get_saga_manager()
    saga_manager.register_event_saga(_GROUP_REBALANCE_SAGA)
    logger.info("group_rebalance_saga_registered")


def _load_configuration(logger: logging.Logger, config_path: Optional[str] = None) -> None:
    """Load provider configuration from file or use defaults."""
    # Priority: --config arg > MCP_CONFIG env > default config.yaml
    if config_path is None:
        config_path = os.getenv("MCP_CONFIG", "config.yaml")

    if Path(config_path).exists():
        logger.info(f"loading_config_from_file: {config_path}")
        full_config = load_config_from_file(config_path)
        load_config(full_config.get("providers", {}))
    else:
        logger.info(f"config_not_found: {config_path}, using_default_config")
        load_config(
            {
                "math_subprocess": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "examples.provider_math.server"],
                    "idle_ttl_s": 180,
                },
            }
        )


def _start_background_workers(logger: logging.Logger) -> None:
    """Start GC and health check background workers."""
    gc_worker = BackgroundWorker(PROVIDERS, interval_s=30, task="gc")
    gc_worker.start()

    health_worker = BackgroundWorker(PROVIDERS, interval_s=60, task="health_check")
    health_worker.start()

    logger.info("background_workers_started: gc, health_check")


def _run_http_server(http_host: str, http_port: int, logger: logging.Logger) -> None:
    """Run HTTP server mode."""
    logger.info(f"Starting HTTP server on {http_host}:{http_port}")
    from .fastmcp_server import run_fastmcp_server, setup_fastmcp_server

    setup_fastmcp_server(
        registry_list_fn=registry_list,
        registry_start_fn=registry_start,
        registry_stop_fn=registry_stop,
        registry_tools_fn=registry_tools,
        registry_invoke_fn=registry_invoke,
        registry_details_fn=registry_details,
        registry_health_fn=registry_health,
    )
    run_fastmcp_server()


def _run_stdio_server(logger: logging.Logger) -> None:
    """Run stdio server mode."""
    logger.info("Starting stdio server (FastMCP)")
    try:
        mcp.run()
    except Exception as e:
        logger.error(f"mcp_server_error: {e}")
        import time

        while True:
            time.sleep(60)


def main():
    """Main entry point for the registry server."""
    args = _parse_args()

    http_mode = args.http or os.getenv("MCP_MODE", "stdio") == "http"
    http_host = args.host or os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    http_port = args.port or int(os.getenv("MCP_HTTP_PORT", "8000"))
    config_path = args.config  # May be None

    # Load config first to get logging settings
    log_level = logging.INFO
    log_file = None

    if config_path and Path(config_path).exists():
        try:
            full_config = load_config_from_file(config_path)
            logging_config = full_config.get("logging", {})
            level_str = logging_config.get("level", "INFO").upper()
            log_level = getattr(logging, level_str, logging.INFO)
            log_file = logging_config.get("file")
        except Exception:
            pass  # Will use defaults

    setup_logging(level=log_level, log_file=log_file)
    logger = logging.getLogger(__name__)
    logger.info(f"mcp_registry_starting mode={'http' if http_mode else 'stdio'}")

    _ensure_data_dir(logger)

    # Initialize runtime and wire up global references
    runtime = create_runtime()
    global PROVIDER_REPOSITORY, PROVIDERS
    PROVIDER_REPOSITORY = runtime.repository
    PROVIDERS = ProviderDict(PROVIDER_REPOSITORY)

    # Initialize components
    _init_event_handlers(runtime.event_bus, runtime.security_handler, logger)
    _init_cqrs(runtime, logger)
    _init_saga(logger)

    logger.info(
        f"security_config: rate_limit_rps={runtime.rate_limit_config.requests_per_second}, "
        f"burst_size={runtime.rate_limit_config.burst_size}"
    )

    # Load configuration and start workers
    _load_configuration(logger, config_path)
    _start_background_workers(logger)

    # Log ready state
    provider_ids = list(PROVIDERS.keys())
    group_ids = list(GROUPS.keys())
    logger.info(f"mcp_registry_ready: providers={provider_ids}, groups={group_ids}")
    print(
        f"mcp_registry_ready: providers={provider_ids}, groups={group_ids}",
        file=sys.stderr,
        flush=True,
    )

    # Run server
    if http_mode:
        _run_http_server(http_host, http_port, logger)
    else:
        _run_stdio_server(logger)


if __name__ == "__main__":
    main()
