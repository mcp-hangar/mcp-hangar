"""Discovery orchestrator initialization."""

from pathlib import Path
from typing import Any

from ...application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from ...domain.discovery import DiscoveryMode
from ...domain.model import Provider
from ...domain.security.input_validator import InputValidator
from ...logging_config import get_logger
from ..state import PROVIDERS, set_discovery_orchestrator

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

    static_providers = set(PROVIDERS.keys())
    orchestrator_config = DiscoveryConfig.from_dict(discovery_config)
    orchestrator = DiscoveryOrchestrator(
        config=orchestrator_config,
        static_providers=static_providers,
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
        except Exception as e:  # fault-barrier: discovery source init failure must not crash bootstrap
            logger.error(
                "discovery_source_error",
                source_type=source_type,
                error=str(e),
            )

    # Set up registration callbacks
    orchestrator.on_register = _on_provider_register
    orchestrator.on_deregister = _on_provider_deregister

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
        from ...infrastructure.discovery import EntrypointDiscoverySource

        return EntrypointDiscoverySource(
            mode=mode,
            group=config.get("group", "mcp.providers"),
        )
    else:
        logger.warning("discovery_unknown_source_type", source_type=source_type)
        return None


async def _on_provider_register(provider) -> bool:
    """Callback when discovery wants to register a provider.

    Args:
        provider: Discovered provider information.

    Returns:
        True if registration succeeded, False otherwise.
    """
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
            logger.warning(
                "unknown_provider_mode_skipping",
                mode=mode,
                provider_name=provider.name,
            )
            return False

        provider_kwargs = {
            "provider_id": provider.name,
            "mode": provider_mode,
            "description": f"Discovered from {provider.source_type}",
        }

        if provider_mode == "docker":
            image = conn_info.get("image")
            if not image:
                logger.warning(
                    "container_provider_no_image_skipping",
                    provider_name=provider.name,
                )
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
                logger.warning(
                    "http_provider_no_endpoint_skipping",
                    provider_name=provider.name,
                )
                return False
        else:
            command = conn_info.get("command")
            if not command:
                logger.warning(
                    "subprocess_provider_no_command_skipping",
                    provider_name=provider.name,
                )
                return False
            provider_kwargs["command"] = command

        provider_kwargs["env"] = conn_info.get("env", {})

        new_provider = Provider(**provider_kwargs)
        PROVIDERS[provider.name] = new_provider
        logger.info(
            "discovery_registered_provider",
            provider_name=provider.name,
            mode=provider_mode,
        )
        return True
    except Exception as e:  # fault-barrier: registration failure must not crash discovery cycle
        logger.error(
            "discovery_registration_failed",
            provider_name=provider.name,
            error=str(e),
        )
        return False


async def _on_provider_deregister(name: str, reason: str):
    """Callback when discovery wants to deregister a provider.

    Args:
        name: Provider name to deregister.
        reason: Reason for deregistration.
    """
    try:
        if name in PROVIDERS:
            provider = PROVIDERS.get(name)
            if provider:
                provider.stop()
            del PROVIDERS._repo._providers[name]
            logger.info(
                "discovery_deregistered_provider",
                provider_name=name,
                reason=reason,
            )
    except Exception as e:  # fault-barrier: deregistration failure must not crash discovery cycle
        logger.error(
            "discovery_deregistration_failed",
            provider_name=name,
            error=str(e),
        )


def _auto_add_volumes(provider_name: str) -> list:
    """Auto-add persistent volumes for known stateful providers.

    Args:
        provider_name: Provider name to check for known volume patterns.

    Returns:
        List of volume mount strings.
    """
    volumes = []
    provider_name_lower = provider_name.lower()
    data_base = Path("./data").absolute()

    try:
        if "memory" in provider_name_lower:
            memory_dir = data_base / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_dir.chmod(0o777)
            volumes.append(f"{memory_dir}:/app/data:rw")
            logger.info(
                "auto_added_memory_volume",
                provider_name=provider_name,
                volume=f"{memory_dir}:/app/data",
            )

        elif "filesystem" in provider_name_lower:
            fs_dir = data_base / "filesystem"
            fs_dir.mkdir(parents=True, exist_ok=True)
            fs_dir.chmod(0o777)
            volumes.append(f"{fs_dir}:/data:rw")
            logger.info(
                "auto_added_filesystem_volume",
                provider_name=provider_name,
                volume=f"{fs_dir}:/data",
            )
    except OSError as e:
        logger.warning(
            "auto_volume_creation_failed",
            provider_name=provider_name,
            error=str(e),
        )

    return volumes
