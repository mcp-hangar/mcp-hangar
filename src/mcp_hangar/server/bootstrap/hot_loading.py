"""Hot-loading components initialization."""

from typing import Any, TYPE_CHECKING

from ...application.commands.load_handlers import LoadMcpServerHandler, UnloadMcpServerHandler
from ...application.services.package_resolver import PackageResolver
from ...application.services.secrets_resolver import SecretsResolver
from ...domain.contracts.installer import IPackageInstaller
from ...domain.model import McpServer
from ...infrastructure.installers import npx_installer, runtime_availability, uvx_installer
from ...logging_config import get_logger
from ..state import get_runtime, get_runtime_mcp_servers

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def _approval_gate_available() -> bool:
    """Whether the approval gate is attached to the application context.

    The same lookup `_check_approval_gate` makes on the invoke path, so "the
    load was allowed to gate a tool" and "the gate holds the call" cannot
    disagree.
    """
    from ..context import get_context

    return getattr(get_context(), "approval_gate", None) is not None


def init_hot_loading(
    runtime: "Runtime",
    config: dict[str, Any],
) -> tuple[LoadMcpServerHandler | None, UnloadMcpServerHandler | None]:
    """Initialize hot-loading components for runtime mcp_server injection.

    Args:
        runtime: Runtime instance.
        config: Full configuration dictionary.

    Returns:
        Tuple of (LoadMcpServerHandler, UnloadMcpServerHandler) or (None, None) if disabled.
    """
    hot_loading_config = config.get("hot_loading", {})
    if not hot_loading_config.get("enabled", True):
        logger.info("hot_loading_disabled")
        return None, None

    try:
        from ...infrastructure.registry import RegistryCache, RegistryClient

        # Read config values
        registry_config = hot_loading_config.get("registry", {})
        cache_config = hot_loading_config.get("cache", {})

        # Create cache with config
        cache = RegistryCache(
            ttl_seconds=cache_config.get("ttl_s", 3600),
            max_entries=cache_config.get("max_entries", 1000),
        )

        # Create registry client with config
        registry_client = RegistryClient(
            base_url=registry_config.get("base_url", RegistryClient.DEFAULT_BASE_URL),
            timeout=registry_config.get("timeout_s", RegistryClient.DEFAULT_TIMEOUT),
            max_retries=registry_config.get("max_retries", RegistryClient.DEFAULT_MAX_RETRIES),
            cache=cache,
        )

        # Every field of this used to be hardcoded `False`, which made
        # `PackageResolver` filter out every package and `hangar_load` answer
        # "No compatible package found (missing runtime?)" on every call, with
        # "Available runtimes: []" in the warnings. Asked of the installers now.
        installers: list[IPackageInstaller] = [uvx_installer(), npx_installer()]
        package_resolver = PackageResolver(runtime_availability(installers))

        secrets_resolver = SecretsResolver()

        runtime_store = get_runtime_mcp_servers()

        def mcp_server_factory(**kwargs):
            return McpServer(**kwargs)

        load_handler = LoadMcpServerHandler(
            registry_client=registry_client,
            package_resolver=package_resolver,
            secrets_resolver=secrets_resolver,
            installers=installers,
            runtime_store=runtime_store,
            event_bus=runtime.event_bus,
            mcp_server_factory=mcp_server_factory,
            mcp_server_repository=get_runtime().repository,
            # Asked at load time, not now: the gate is attached to the context
            # later in bootstrap, so a bool captured here would always be False.
            approval_gate_available=_approval_gate_available,
        )

        unload_handler = UnloadMcpServerHandler(
            runtime_store=runtime_store,
            event_bus=runtime.event_bus,
        )

        logger.info("hot_loading_initialized")
        return load_handler, unload_handler

    except ImportError as e:
        logger.warning(
            "hot_loading_unavailable",
            error=str(e),
            suggestion="Install httpx for registry client support",
        )
        return None, None
