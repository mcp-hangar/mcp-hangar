"""Discovery orchestrator initialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ...application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from ...domain.discovery import DiscoveryMode
from ...domain.model import McpServer
from ...domain.security.input_validator import InputValidator
from ...logging_config import get_logger
from ..state import get_runtime, set_discovery_orchestrator

logger = get_logger(__name__)


def create_discovery_orchestrator(config: dict[str, Any]) -> DiscoveryOrchestrator | None:
    """Create discovery orchestrator from config (not started).

    Args:
        config: Full configuration dictionary.

    Returns:
        DiscoveryOrchestrator instance or None if disabled.
    """
    discovery_config = config.get("discovery", {})
    if not discovery_config.get("enabled", False):
        logger.info("discovery_disabled")
        return None

    logger.info("discovery_initializing")

    repository = get_runtime().repository
    static_mcp_servers = set(repository.get_all_ids())
    orchestrator_config = DiscoveryConfig.from_dict(discovery_config)
    orchestrator = DiscoveryOrchestrator(
        config=orchestrator_config,
        static_mcp_servers=static_mcp_servers,
        input_validator=InputValidator(),
    )

    sources_config = discovery_config.get("sources", [])
    for source_config in sources_config:
        source_type = source_config.get("type")
        try:
            source = _create_discovery_source(source_type, source_config)
            if source:
                orchestrator.add_source(source)
        except ImportError as e:
            logger.warning(
                "discovery_source_unavailable",
                source_type=source_type,
                error=str(e),
            )
        except Exception as e:  # noqa: BLE001 -- fault-barrier: discovery source init failure must not crash bootstrap
            logger.error(
                "discovery_source_error",
                source_type=source_type,
                error=str(e),
            )

    # Set up registration callbacks
    orchestrator.on_register = _on_mcp_server_register
    orchestrator.on_deregister = _on_mcp_server_deregister

    set_discovery_orchestrator(orchestrator)
    return orchestrator


def _create_discovery_source(source_type: str, config: dict[str, Any]):
    """Create a discovery source based on type and config.

    Args:
        source_type: Type of discovery source (kubernetes, docker, filesystem, entrypoint).
        config: Source configuration dictionary.

    Returns:
        Discovery source instance or None.
    """
    mode_str = config.get("mode", "additive")
    mode = DiscoveryMode.AUTHORITATIVE if mode_str == "authoritative" else DiscoveryMode.ADDITIVE

    if source_type == "kubernetes":
        from ...infrastructure.discovery import KubernetesDiscoverySource

        return KubernetesDiscoverySource(
            mode=mode,
            namespaces=config.get("namespaces"),
            label_selector=config.get("label_selector"),
            in_cluster=config.get("in_cluster", True),
        )
    elif source_type == "docker":
        from ...infrastructure.discovery import DockerDiscoverySource

        return DockerDiscoverySource(
            mode=mode,
            socket_path=config.get("socket_path"),
        )
    elif source_type == "filesystem":
        from ...infrastructure.discovery import FilesystemDiscoverySource

        path = config.get("path", "/etc/mcp-hangar/mcp_servers.d/")
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
        from ...infrastructure.discovery import EntrypointDiscoverySource

        return EntrypointDiscoverySource(
            mode=mode,
            group=config.get("group", "mcp.mcp_servers"),
        )
    else:
        logger.warning("discovery_unknown_source_type", source_type=source_type)
        return None


async def _on_mcp_server_register(mcp_server) -> bool:
    """Callback when discovery wants to register a mcp_server.

    Args:
        mcp_server: Discovered mcp_server information.

    Returns:
        True if registration succeeded, False otherwise.
    """
    try:
        conn_info = mcp_server.connection_info
        mode = mcp_server.mode

        if mode == "container":
            mcp_server_mode = "docker"
        elif mode in ("http", "sse"):
            mcp_server_mode = "remote"
        elif mode in ("subprocess", "docker", "remote"):
            mcp_server_mode = mode
        else:
            logger.warning(
                "unknown_mcp_server_mode_skipping",
                mode=mode,
                mcp_server_name=mcp_server.name,
            )
            return False

        mcp_server_kwargs: dict[str, Any] = {
            "mcp_server_id": mcp_server.name,
            "mode": mcp_server_mode,
            "description": f"Discovered from {mcp_server.source_type}",
        }

        if mcp_server_mode == "docker":
            image = conn_info.get("image")
            if not image:
                logger.warning(
                    "container_mcp_server_no_image_skipping",
                    mcp_server_name=mcp_server.name,
                )
                return False
            mcp_server_kwargs["image"] = image
            mcp_server_kwargs["read_only"] = conn_info.get("read_only", False)
            if conn_info.get("command"):
                mcp_server_kwargs["command"] = conn_info.get("command")

            volumes = conn_info.get("volumes", [])
            if not volumes:
                volumes = _auto_add_volumes(mcp_server.name)
            if volumes:
                mcp_server_kwargs["volumes"] = volumes

        elif mcp_server_mode == "remote":
            host = conn_info.get("host")
            port = conn_info.get("port")
            endpoint = conn_info.get("endpoint")
            if endpoint:
                mcp_server_kwargs["endpoint"] = endpoint
            elif host and port:
                mcp_server_kwargs["endpoint"] = f"http://{host}:{port}"
            else:
                logger.warning(
                    "http_mcp_server_no_endpoint_skipping",
                    mcp_server_name=mcp_server.name,
                )
                return False
        else:
            command = conn_info.get("command")
            if not command:
                logger.warning(
                    "subprocess_mcp_server_no_command_skipping",
                    mcp_server_name=mcp_server.name,
                )
                return False
            mcp_server_kwargs["command"] = command

        mcp_server_kwargs["env"] = conn_info.get("env", {})

        new_mcp_server = McpServer(**cast(Any, mcp_server_kwargs))
        get_runtime().repository.add(mcp_server.name, new_mcp_server)
        logger.info(
            "discovery_registered_mcp_server",
            mcp_server_name=mcp_server.name,
            mode=mcp_server_mode,
        )
        return True
    except Exception as e:  # noqa: BLE001 -- fault-barrier: registration failure must not crash discovery cycle
        logger.error(
            "discovery_registration_failed",
            mcp_server_name=mcp_server.name,
            error=str(e),
        )
        return False


async def _on_mcp_server_deregister(name: str, reason: str):
    """Callback when discovery wants to deregister a mcp_server.

    Args:
        name: McpServer name to deregister.
        reason: Reason for deregistration.
    """
    try:
        repository = get_runtime().repository
        if repository.exists(name):
            mcp_server = repository.get(name)
            if mcp_server:
                mcp_server.stop()
            _ = repository.remove(name)
            logger.info(
                "discovery_deregistered_mcp_server",
                mcp_server_name=name,
                reason=reason,
            )
    except Exception as e:  # noqa: BLE001 -- fault-barrier: deregistration failure must not crash discovery cycle
        logger.error(
            "discovery_deregistration_failed",
            mcp_server_name=name,
            error=str(e),
        )


def _auto_add_volumes(mcp_server_name: str) -> list[str]:
    """Auto-add persistent volumes for known stateful mcp_servers.

    Args:
        mcp_server_name: McpServer name to check for known volume patterns.

    Returns:
        List of volume mount strings.
    """
    volumes = []
    mcp_server_name_lower = mcp_server_name.lower()
    data_base = Path("./data").absolute()

    try:
        if "memory" in mcp_server_name_lower:
            memory_dir = data_base / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_dir.chmod(0o777)
            volumes.append(f"{memory_dir}:/app/data:rw")
            logger.info(
                "auto_added_memory_volume",
                mcp_server_name=mcp_server_name,
                volume=f"{memory_dir}:/app/data",
            )

        elif "filesystem" in mcp_server_name_lower:
            fs_dir = data_base / "filesystem"
            fs_dir.mkdir(parents=True, exist_ok=True)
            fs_dir.chmod(0o777)
            volumes.append(f"{fs_dir}:/data:rw")
            logger.info(
                "auto_added_filesystem_volume",
                mcp_server_name=mcp_server_name,
                volume=f"{fs_dir}:/data",
            )
    except OSError as e:
        logger.warning(
            "auto_volume_creation_failed",
            mcp_server_name=mcp_server_name,
            error=str(e),
        )

    return volumes
