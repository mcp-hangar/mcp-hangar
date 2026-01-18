"""Python Entrypoint Discovery Source.

Discovers MCP providers from Python package entry points.
Uses the standard entry_points mechanism from importlib.metadata.

Entry Point Group: mcp.providers

Example pyproject.toml:
    [project.entry-points."mcp.providers"]
    my_provider = "my_package.mcp_server:create_server"
"""

from typing import Any

from mcp_hangar.domain.discovery.discovered_provider import DiscoveredProvider
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource

from ...logging_config import get_logger

logger = get_logger(__name__)

# Import metadata handling
try:
    from importlib.metadata import EntryPoint, entry_points

    METADATA_AVAILABLE = True
except ImportError:
    try:
        from importlib_metadata import EntryPoint, entry_points

        METADATA_AVAILABLE = True
    except ImportError:
        METADATA_AVAILABLE = False
        logger.debug("importlib.metadata not available, EntrypointDiscoverySource unavailable")


class EntrypointDiscoverySource(DiscoverySource):
    """Discover MCP providers from Python package entry points.

    Scans installed Python packages for entry points in the specified group.
    Each entry point should reference a factory function that creates an MCP server.

    Entry Point Format:
        The entry point value should be a module path to a factory function.
        The function can optionally return configuration metadata.

    Example:
        # In pyproject.toml
        [project.entry-points."mcp.providers"]
        my_tools = "my_package.server:create_server"

        # In my_package/server.py
        def create_server():
            '''Returns MCP server configuration.'''
            return {
                "name": "my-tools",
                "mode": "subprocess",
                "command": ["python", "-m", "my_package.server"],
                "metadata": {"version": "1.0.0"}
            }
    """

    DEFAULT_GROUP = "mcp.providers"

    def __init__(
        self,
        group: str = DEFAULT_GROUP,
        mode: DiscoveryMode = DiscoveryMode.ADDITIVE,
        default_ttl: int = 90,
    ):
        """Initialize entrypoint discovery source.

        Args:
            group: Entry point group name (default: mcp.providers)
            mode: Discovery mode (default: additive)
            default_ttl: Default TTL for discovered providers
        """
        super().__init__(mode)

        if not METADATA_AVAILABLE:
            raise ImportError(
                "importlib.metadata is required for EntrypointDiscoverySource. "
                "Install with: pip install importlib-metadata"
            )

        self.group = group
        self.default_ttl = default_ttl

    @property
    def source_type(self) -> str:
        return "entrypoint"

    async def discover(self) -> list[DiscoveredProvider]:
        """Discover providers from Python entry points.

        Returns:
            List of discovered providers
        """
        providers = []

        try:
            # Get entry points for our group
            eps = entry_points()

            # Handle different Python versions
            if hasattr(eps, "select"):
                # Python 3.10+
                group_eps = eps.select(group=self.group)
            elif hasattr(eps, "get"):
                # Python 3.9
                group_eps = eps.get(self.group, [])
            else:
                # Fallback
                group_eps = getattr(eps, self.group, [])

            for ep in group_eps:
                try:
                    provider = await self._load_entrypoint(ep)
                    if provider:
                        providers.append(provider)
                        await self.on_provider_discovered(provider)
                except Exception as e:
                    logger.error(f"Failed to load entry point {ep.name}: {e}")

        except Exception as e:
            logger.error(f"Entry point discovery failed: {e}")
            raise

        logger.debug(f"Entrypoint discovery found {len(providers)} providers")
        return providers

    async def _load_entrypoint(self, ep: EntryPoint) -> DiscoveredProvider | None:
        """Load and parse an entry point.

        Args:
            ep: Entry point to load

        Returns:
            DiscoveredProvider or None if invalid
        """
        try:
            # Load the entry point
            factory = ep.load()

            # Call factory if it's callable
            if callable(factory):
                try:
                    config = factory()
                except Exception as e:
                    logger.warning(f"Entry point {ep.name} factory failed: {e}")
                    config = None
            else:
                config = factory

            # Build provider from config or defaults
            return self._build_provider(ep, config)

        except Exception as e:
            logger.error(f"Error loading entry point {ep.name}: {e}")
            return None

    def _build_provider(self, ep: EntryPoint, config: dict[str, Any] | None) -> DiscoveredProvider:
        """Build provider from entry point and optional config.

        Args:
            ep: Entry point
            config: Optional configuration from factory

        Returns:
            DiscoveredProvider instance
        """
        config = config or {}

        # Extract name (prefer config, fall back to entry point name)
        name = config.get("name", ep.name)

        # Determine mode
        mode = config.get("mode", "subprocess")

        # Build connection info
        connection_info = {}

        if mode in ("subprocess", "stdio"):
            # Default command is to run as module
            command = config.get("command")
            if not command:
                # Parse entry point value to get module
                module_path = ep.value.rsplit(":", 1)[0] if ":" in ep.value else ep.value
                command = ["python", "-m", module_path]
            connection_info["command"] = command

            if "env" in config:
                connection_info["env"] = config["env"]

        elif mode in ("http", "sse", "remote"):
            connection_info["host"] = config.get("host", "localhost")
            connection_info["port"] = config.get("port", 8080)
            connection_info["health_path"] = config.get("health_path", "/health")

        # Copy additional connection settings
        for key in ("timeout", "endpoint"):
            if key in config:
                connection_info[key] = config[key]

        # Build metadata
        metadata = {
            "entrypoint_name": ep.name,
            "entrypoint_value": ep.value,
            "entrypoint_group": self.group,
            **config.get("metadata", {}),
        }

        # Add package info if available
        if hasattr(ep, "dist") and ep.dist:
            metadata["package_name"] = ep.dist.name
            metadata["package_version"] = ep.dist.version

        ttl = config.get("ttl", self.default_ttl)

        return DiscoveredProvider.create(
            name=name,
            source_type=self.source_type,
            mode=mode,
            connection_info=connection_info,
            metadata=metadata,
            ttl_seconds=ttl,
        )

    async def health_check(self) -> bool:
        """Check if entry points can be read.

        Returns:
            True if metadata system is available
        """
        try:
            # Just check that we can access entry_points
            entry_points()
            return True
        except Exception as e:
            logger.warning(f"Entrypoint health check failed: {e}")
            return False

    async def start(self) -> None:
        """Start the entrypoint discovery source."""
        logger.info(f"Entrypoint discovery source started (group={self.group})")

    async def stop(self) -> None:
        """Stop the entrypoint discovery source."""
        logger.info("Entrypoint discovery source stopped")
