"""MCP Registry Server package.

This module provides a modular implementation of the MCP Registry server.
The server is split into:
- state.py: Global state (providers, groups, runtime)
- validation.py: Input validation and error handling
- config.py: Configuration loading
- tools/: MCP tool implementations

Usage:
    from mcp_hangar.server import main
    main()
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from ..application.commands import register_all_handlers as register_command_handlers
from ..application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from ..application.event_handlers import AlertEventHandler, AuditEventHandler, LoggingEventHandler, MetricsEventHandler
from ..application.queries import register_all_handlers as register_query_handlers
from ..application.sagas import GroupRebalanceSaga
from ..domain.discovery import DiscoveryMode
from ..domain.model import Provider
from ..gc import BackgroundWorker
from ..infrastructure.saga_manager import get_saga_manager
from ..logging_config import get_logger, setup_logging
from .config import load_config, load_config_from_file, load_configuration
from .context import get_context, init_context
from .state import (
    COMMAND_BUS,
    EVENT_BUS,
    get_discovery_orchestrator,
    get_runtime,
    GROUPS,
    PROVIDER_REPOSITORY,
    PROVIDERS,
    QUERY_BUS,
    set_discovery_orchestrator,
    set_group_rebalance_saga,
)
from .tools import (
    register_discovery_tools,
    register_group_tools,
    register_health_tools,
    register_provider_tools,
    register_registry_tools,
    registry_list,
)

logger = get_logger(__name__)

# =============================================================================
# Constants
# =============================================================================

GC_WORKER_INTERVAL_SECONDS = 30
"""Interval for garbage collection worker."""

HEALTH_CHECK_INTERVAL_SECONDS = 60
"""Interval for health check worker."""

# Initialize MCP server
mcp = FastMCP("mcp-registry")


def _parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="MCP Registry Server")
    parser.add_argument("--http", action="store_true", help="Run HTTP server mode")
    parser.add_argument("--host", type=str, default=None, help="HTTP server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="HTTP server port (default: 8000)")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml file")
    return parser.parse_args()


def _ensure_data_dir() -> None:
    """Ensure data directory exists for persistent storage."""
    data_dir = Path("./data")
    if not data_dir.exists():
        try:
            data_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
            logger.info("data_directory_created", path=str(data_dir.absolute()))
        except OSError as e:
            logger.warning("data_directory_creation_failed", error=str(e))


def _init_event_handlers() -> None:
    """Initialize and register event handlers."""
    runtime = get_runtime()

    logging_handler = LoggingEventHandler()
    runtime.event_bus.subscribe_to_all(logging_handler.handle)

    metrics_handler = MetricsEventHandler()
    runtime.event_bus.subscribe_to_all(metrics_handler.handle)

    alert_handler = AlertEventHandler()
    runtime.event_bus.subscribe_to_all(alert_handler.handle)

    audit_handler = AuditEventHandler()
    runtime.event_bus.subscribe_to_all(audit_handler.handle)

    runtime.event_bus.subscribe_to_all(runtime.security_handler.handle)

    logger.info(
        "event_handlers_registered",
        handlers=["logging", "metrics", "alert", "audit", "security"],
    )


def _init_cqrs() -> None:
    """Initialize CQRS command and query handlers."""
    runtime = get_runtime()
    register_command_handlers(runtime.command_bus, PROVIDER_REPOSITORY, runtime.event_bus)
    register_query_handlers(runtime.query_bus, PROVIDER_REPOSITORY)
    logger.info("cqrs_handlers_registered")


def _init_saga() -> None:
    """Initialize group rebalance saga."""
    ctx = get_context()
    saga = GroupRebalanceSaga(groups=ctx.groups)
    ctx.group_rebalance_saga = saga
    set_group_rebalance_saga(saga)  # For backward compatibility
    saga_manager = get_saga_manager()
    saga_manager.register_event_saga(saga)
    logger.info("group_rebalance_saga_registered")


async def _init_discovery(config: Dict[str, Any]) -> None:
    """Initialize provider discovery if enabled in config."""
    discovery_config = config.get("discovery", {})
    if not discovery_config.get("enabled", False):
        logger.info("discovery_disabled")
        return

    logger.info("discovery_initializing")

    static_providers = set(PROVIDERS.keys())
    orchestrator_config = DiscoveryConfig.from_dict(discovery_config)
    orchestrator = DiscoveryOrchestrator(config=orchestrator_config, static_providers=static_providers)

    sources_config = discovery_config.get("sources", [])
    for source_config in sources_config:
        source_type = source_config.get("type")
        try:
            source = _create_discovery_source(source_type, source_config)
            if source:
                orchestrator.add_source(source)
        except ImportError as e:
            logger.warning("discovery_source_unavailable", source_type=source_type, error=str(e))
        except Exception as e:
            logger.error("discovery_source_error", source_type=source_type, error=str(e))

    # Set up registration callbacks
    orchestrator.on_register = _on_provider_register
    orchestrator.on_deregister = _on_provider_deregister

    await orchestrator.start()
    set_discovery_orchestrator(orchestrator)

    stats = orchestrator.get_stats()
    logger.info("discovery_started", sources_count=stats["sources_count"])


def _create_discovery_source(source_type: str, config: Dict[str, Any]):
    """Create a discovery source based on type and config."""
    mode_str = config.get("mode", "additive")
    mode = DiscoveryMode.AUTHORITATIVE if mode_str == "authoritative" else DiscoveryMode.ADDITIVE

    if source_type == "kubernetes":
        from ..infrastructure.discovery import KubernetesDiscoverySource

        return KubernetesDiscoverySource(
            mode=mode,
            namespaces=config.get("namespaces"),
            label_selector=config.get("label_selector"),
            in_cluster=config.get("in_cluster", True),
        )
    elif source_type == "docker":
        from ..infrastructure.discovery import DockerDiscoverySource

        return DockerDiscoverySource(
            mode=mode,
            socket_path=config.get("socket_path"),
        )
    elif source_type == "filesystem":
        from ..infrastructure.discovery import FilesystemDiscoverySource

        path = config.get("path", "/etc/mcp-hangar/providers.d/")
        resolved_path = Path(path)
        if not resolved_path.is_absolute():
            resolved_path = Path.cwd() / resolved_path
        return FilesystemDiscoverySource(
            mode=mode,
            path=str(resolved_path),
            pattern=config.get("pattern", "*.yaml"),
            watch=config.get("watch", True),
        )
    elif source_type == "entrypoint":
        from ..infrastructure.discovery import EntrypointDiscoverySource

        return EntrypointDiscoverySource(
            mode=mode,
            group=config.get("group", "mcp.providers"),
        )
    else:
        logger.warning("discovery_unknown_source_type", source_type=source_type)
        return None


async def _on_provider_register(provider) -> bool:
    """Callback when discovery wants to register a provider."""
    try:
        conn_info = provider.connection_info
        mode = provider.mode

        if mode == "container":
            provider_mode = "docker"
        elif mode in ("http", "sse"):
            provider_mode = "remote"
        elif mode in ("subprocess", "docker", "remote"):
            provider_mode = mode
        else:
            logger.warning("unknown_provider_mode_skipping", mode=mode, provider_name=provider.name)
            return False

        provider_kwargs = {
            "provider_id": provider.name,
            "mode": provider_mode,
            "description": f"Discovered from {provider.source_type}",
        }

        if provider_mode == "docker":
            image = conn_info.get("image")
            if not image:
                logger.warning("container_provider_no_image_skipping", provider_name=provider.name)
                return False
            provider_kwargs["image"] = image
            provider_kwargs["read_only"] = conn_info.get("read_only", False)
            if conn_info.get("command"):
                provider_kwargs["command"] = conn_info.get("command")

            volumes = conn_info.get("volumes", [])
            if not volumes:
                volumes = _auto_add_volumes(provider.name)
            if volumes:
                provider_kwargs["volumes"] = volumes

        elif provider_mode == "remote":
            host = conn_info.get("host")
            port = conn_info.get("port")
            endpoint = conn_info.get("endpoint")
            if endpoint:
                provider_kwargs["endpoint"] = endpoint
            elif host and port:
                provider_kwargs["endpoint"] = f"http://{host}:{port}"
            else:
                logger.warning("http_provider_no_endpoint_skipping", provider_name=provider.name)
                return False
        else:
            command = conn_info.get("command")
            if not command:
                logger.warning("subprocess_provider_no_command_skipping", provider_name=provider.name)
                return False
            provider_kwargs["command"] = command

        provider_kwargs["env"] = conn_info.get("env", {})

        new_provider = Provider(**provider_kwargs)
        PROVIDERS[provider.name] = new_provider
        logger.info("discovery_registered_provider", provider_name=provider.name, mode=provider_mode)
        return True
    except Exception as e:
        logger.error("discovery_registration_failed", provider_name=provider.name, error=str(e))
        return False


async def _on_provider_deregister(name: str, reason: str):
    """Callback when discovery wants to deregister a provider."""
    try:
        if name in PROVIDERS:
            provider = PROVIDERS.get(name)
            if provider:
                provider.stop()
            del PROVIDERS._repo._providers[name]
            logger.info("discovery_deregistered_provider", provider_name=name, reason=reason)
    except Exception as e:
        logger.error("discovery_deregistration_failed", provider_name=name, error=str(e))


def _auto_add_volumes(provider_name: str) -> list:
    """Auto-add persistent volumes for known stateful providers."""
    volumes = []
    provider_name_lower = provider_name.lower()
    data_base = Path("./data").absolute()

    if "memory" in provider_name_lower:
        memory_dir = data_base / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_dir.chmod(0o777)
        volumes.append(f"{memory_dir}:/app/data:rw")
        logger.info("auto_added_memory_volume", provider_name=provider_name, volume=f"{memory_dir}:/app/data")

    elif "filesystem" in provider_name_lower:
        fs_dir = data_base / "filesystem"
        fs_dir.mkdir(parents=True, exist_ok=True)
        fs_dir.chmod(0o777)
        volumes.append(f"{fs_dir}:/data:rw")
        logger.info("auto_added_filesystem_volume", provider_name=provider_name, volume=f"{fs_dir}:/data")

    return volumes


def _start_background_workers() -> None:
    """Start GC and health check background workers."""
    gc_worker = BackgroundWorker(PROVIDERS, interval_s=GC_WORKER_INTERVAL_SECONDS, task="gc")
    gc_worker.start()

    health_worker = BackgroundWorker(PROVIDERS, interval_s=HEALTH_CHECK_INTERVAL_SECONDS, task="health_check")
    health_worker.start()

    logger.info("background_workers_started", workers=["gc", "health_check"])


def _register_all_tools() -> None:
    """Register all MCP tools."""
    register_registry_tools(mcp)
    register_provider_tools(mcp)
    register_health_tools(mcp)
    register_discovery_tools(mcp)
    register_group_tools(mcp)
    logger.info("mcp_tools_registered")


def _run_http_server(http_host: str, http_port: int) -> None:
    """Run HTTP server mode using FastMCP.

    In HTTP mode, we use FastMCP's built-in HTTP handling.
    The MCP tools are already registered via _register_all_tools().
    """
    logger.info("starting_http_server", host=http_host, port=http_port)

    import uvicorn

    # FastMCP provides an ASGI app
    uvicorn.run(
        mcp.sse_app(),
        host=http_host,
        port=http_port,
        log_level="info",
    )


def _run_stdio_server() -> None:
    """Run stdio server mode."""
    logger.info("starting_stdio_server")
    try:
        mcp.run()
    except Exception as e:
        logger.error("mcp_server_error", error=str(e), error_type=type(e).__name__)
        import time

        while True:
            time.sleep(60)


def main():
    """Main entry point for the registry server."""
    args = _parse_args()

    http_mode = args.http or os.getenv("MCP_MODE", "stdio") == "http"
    http_host = args.host or os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    http_port = args.port or int(os.getenv("MCP_HTTP_PORT", "8000"))
    config_path = args.config

    # Load config first to get logging settings
    log_level = "INFO"
    log_file = None
    json_format = os.getenv("MCP_JSON_LOGS", "false").lower() == "true"

    if config_path and Path(config_path).exists():
        try:
            full_config = load_config_from_file(config_path)
            logging_config = full_config.get("logging", {})
            log_level = logging_config.get("level", "INFO").upper()
            log_file = logging_config.get("file")
            json_format = logging_config.get("json_format", json_format)
        except Exception:
            pass

    setup_logging(level=log_level, json_format=json_format, log_file=log_file)
    logger.info("mcp_registry_starting", mode="http" if http_mode else "stdio")

    _ensure_data_dir()

    # Initialize application context (DI container)
    runtime = get_runtime()
    init_context(runtime)

    # Initialize components
    _init_event_handlers()
    _init_cqrs()
    _init_saga()

    logger.info(
        "security_config_loaded",
        rate_limit_rps=runtime.rate_limit_config.requests_per_second,
        burst_size=runtime.rate_limit_config.burst_size,
    )

    # Load configuration and register tools
    full_config = load_configuration(config_path)
    _register_all_tools()
    _start_background_workers()

    # Initialize discovery if enabled
    asyncio.run(_init_discovery(full_config))

    # Log ready state
    provider_ids = list(PROVIDERS.keys())
    group_ids = list(GROUPS.keys())
    orchestrator = get_discovery_orchestrator()
    discovery_status = "enabled" if orchestrator else "disabled"
    logger.info(
        "mcp_registry_ready",
        providers=provider_ids,
        groups=group_ids,
        discovery=discovery_status,
    )

    # Run server
    if http_mode:
        _run_http_server(http_host, http_port)
    else:
        _run_stdio_server()


__all__ = [
    "main",
    "mcp",
    # Backward compatibility exports
    "load_config",
    "load_config_from_file",
    "PROVIDER_REPOSITORY",
    "PROVIDERS",
    "QUERY_BUS",
    "COMMAND_BUS",
    "EVENT_BUS",
    "GROUPS",
    "registry_list",
]
