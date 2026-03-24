"""Hot-loading components initialization."""

from typing import Any, TYPE_CHECKING

from ...application.commands.load_handlers import LoadProviderHandler, UnloadProviderHandler
from ...application.services.package_resolver import PackageResolver, RuntimeAvailability
from ...application.services.secrets_resolver import SecretsResolver
from ...domain.model import Provider
from ...logging_config import get_logger
from ..state import get_runtime_providers, PROVIDER_REPOSITORY

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_hot_loading(
    runtime: "Runtime",
    config: dict[str, Any],
) -> tuple[LoadProviderHandler | None, UnloadProviderHandler | None]:
    """Initialize hot-loading components for runtime provider injection.

    Args:
        runtime: Runtime instance.
        config: Full configuration dictionary.

    Returns:
        Tuple of (LoadProviderHandler, UnloadProviderHandler) or (None, None) if disabled.
    """
    hot_loading_config = config.get("hot_loading", {})
    if not hot_loading_config.get("enabled", True):
        logger.info("hot_loading_disabled")
        return None, None

    try:
        from ...infrastructure.installers import BinaryInstaller, NpmInstaller, OciInstaller, PyPIInstaller
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

        npm_installer = NpmInstaller()
        pypi_installer = PyPIInstaller()
        oci_installer = OciInstaller()
        binary_installer = BinaryInstaller()

        installers = [pypi_installer, npm_installer, oci_installer, binary_installer]

        availability = RuntimeAvailability(
            pypi=True,
            npm=True,
            oci=True,
            binary=True,
        )
        package_resolver = PackageResolver(availability)

        secrets_resolver = SecretsResolver()

        runtime_store = get_runtime_providers()

        def provider_factory(**kwargs):
            return Provider(**kwargs)

        load_handler = LoadProviderHandler(
            registry_client=registry_client,
            package_resolver=package_resolver,
            secrets_resolver=secrets_resolver,
            installers=installers,
            runtime_store=runtime_store,
            event_bus=runtime.event_bus,
            provider_factory=provider_factory,
            provider_repository=PROVIDER_REPOSITORY,
        )

        unload_handler = UnloadProviderHandler(
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
