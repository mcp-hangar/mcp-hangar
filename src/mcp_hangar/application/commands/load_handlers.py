"""Command handlers for hot-loading mcp_servers from the registry."""

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any

from ...domain.contracts.installer import IPackageInstaller
from ...domain.contracts.registry import IRegistryClient, ServerDetails
from ...domain.events import McpServerHotLoaded, McpServerHotUnloaded, McpServerLoadAttempted, McpServerLoadFailed
from ...domain.exceptions import (
    MissingSecretsError,
    McpServerNotHotLoadedError,
    RegistryAmbiguousSearchError,
    RegistryServerNotFoundError,
    UnverifiedMcpServerError,
)
from ...domain.security.redactor import OutputRedactor
from ...domain.services import get_tool_access_resolver
from ...domain.value_objects import ToolAccessPolicy
from ...domain.contracts.command import CommandHandler
from ...domain.contracts.event_bus import IEventBus
from ...infrastructure.runtime_store import LoadMetadata, RuntimeMcpServerStore
from ...logging_config import get_logger
from ..services.package_resolver import PackageResolver
from ..services.secrets_resolver import SecretsResolver
from .commands import LoadMcpServerCommand, UnloadMcpServerCommand

logger = get_logger(__name__)


def _sanitize_mcp_server_id(server_id: str) -> str:
    """Sanitize server ID to be a valid McpServerId.

    McpServerId only allows alphanumeric characters, hyphens, and underscores.
    This converts dots and slashes to hyphens.

    Args:
        server_id: The server ID from the registry.

    Returns:
        A sanitized mcp_server ID.
    """
    # Replace common separators with hyphens
    sanitized = server_id.replace("/", "-").replace(".", "-")
    # Remove any consecutive hyphens
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip("-")
    return sanitized


@dataclass
class LoadResult:
    """Result of loading a mcp_server.

    Attributes:
        status: Result status ("loaded", "already_loaded", "failed", "missing_secrets").
        mcp_server_id: McpServer ID if loaded.
        mcp_server_name: Server name from registry.
        tools: List of tool summaries if loaded.
        message: Human-readable message.
        warnings: List of warnings.
        instructions: Optional instructions (e.g., for missing secrets).
    """

    status: str
    mcp_server_id: str | None = None
    mcp_server_name: str | None = None
    tools: list[dict[str, Any]] | None = None
    message: str = ""
    warnings: list[str] | None = None
    instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "status": self.status,
            "message": self.message,
        }
        if self.mcp_server_id:
            result["mcp_server_id"] = self.mcp_server_id
        if self.mcp_server_name:
            result["mcp_server_name"] = self.mcp_server_name
        if self.tools is not None:
            result["tools"] = self.tools
        if self.warnings:
            result["warnings"] = self.warnings
        if self.instructions:
            result["instructions"] = self.instructions
        return result


class LoadMcpServerHandler(CommandHandler):
    """Handler for LoadMcpServerCommand.

    Loads a mcp_server from the registry, installs it, and makes it available.
    """

    def __init__(
        self,
        registry_client: IRegistryClient,
        package_resolver: PackageResolver,
        secrets_resolver: SecretsResolver,
        installers: list[IPackageInstaller],
        runtime_store: RuntimeMcpServerStore,
        event_bus: IEventBus,
        mcp_server_factory: callable,
        mcp_server_repository: Any,
    ):
        """Initialize the handler.

        Args:
            registry_client: Client for the MCP registry.
            package_resolver: Resolver for selecting best package.
            secrets_resolver: Resolver for environment secrets.
            installers: List of package installers.
            runtime_store: Store for hot-loaded mcp_servers.
            event_bus: Event bus for publishing events.
            mcp_server_factory: Factory function to create McpServer instances.
            mcp_server_repository: Repository for checking existing mcp_servers.
        """
        self._registry_client = registry_client
        self._package_resolver = package_resolver
        self._secrets_resolver = secrets_resolver
        self._installers = {i.registry_type: i for i in installers}
        self._runtime_store = runtime_store
        self._event_bus = event_bus
        self._mcp_server_factory = mcp_server_factory
        self._mcp_server_repository = mcp_server_repository

    async def handle(self, command: LoadMcpServerCommand) -> LoadResult:
        """Handle the load mcp_server command.

        Args:
            command: The command to handle.

        Returns:
            LoadResult with status and details.
        """
        start_time = time.perf_counter()
        warnings: list[str] = []

        self._event_bus.publish(
            McpServerLoadAttempted(
                mcp_server_name=command.name,
                user_id=command.user_id,
            )
        )

        try:
            # Check both original name and sanitized version
            sanitized_name = _sanitize_mcp_server_id(command.name)

            if self._runtime_store.exists(command.name) or self._runtime_store.exists(sanitized_name):
                return LoadResult(
                    status="already_loaded",
                    mcp_server_id=sanitized_name,
                    message=f"McpServer '{command.name}' is already loaded",
                )

            if self._mcp_server_repository.exists(command.name) or self._mcp_server_repository.exists(sanitized_name):
                return LoadResult(
                    status="already_loaded",
                    mcp_server_id=sanitized_name,
                    message=f"McpServer '{command.name}' is already configured (not hot-loaded)",
                )

            server = await self._find_server(command.name)

            if not server.is_official and not command.force_unverified:
                raise UnverifiedMcpServerError(command.name)

            if not server.is_official:
                warnings.append(f"McpServer '{server.name}' is not officially verified")

            secrets_result = self._secrets_resolver.resolve(
                server.required_env_vars,
                server.id,
            )

            # Create redactor with resolved secrets for error message sanitization
            redactor = OutputRedactor(known_secrets=secrets_result.resolved)

            if not secrets_result.all_resolved:
                instructions = self._secrets_resolver.get_missing_instructions(
                    secrets_result.missing,
                    server.id,
                )
                return LoadResult(
                    status="missing_secrets",
                    mcp_server_name=server.name,
                    message=f"Missing required secrets: {', '.join(secrets_result.missing)}",
                    instructions=instructions,
                )

            package = self._package_resolver.resolve(server.packages)
            if package is None:
                return LoadResult(
                    status="failed",
                    mcp_server_name=server.name,
                    message="No compatible package found (missing runtime?)",
                    warnings=[
                        f"Available packages: {[p.registry_type for p in server.packages]}",
                        f"Available runtimes: {self._package_resolver.get_available_runtimes()}",
                    ],
                )

            installer = self._installers.get(package.registry_type)
            if installer is None:
                return LoadResult(
                    status="failed",
                    mcp_server_name=server.name,
                    message=f"No installer available for package type: {package.registry_type}",
                )

            installed = await installer.install(package)

            # Sanitize mcp_server ID (registry IDs may contain dots/slashes)
            mcp_server_id = _sanitize_mcp_server_id(server.id)

            mcp_server = self._mcp_server_factory(
                mcp_server_id=mcp_server_id,
                mode=installed.mode.value,
                command=installed.command,
                env={**installed.env, **secrets_result.resolved},
            )

            try:
                mcp_server.ensure_ready()
            except Exception:  # noqa: BLE001 -- fault-barrier: cleanup installed package on startup failure, then re-raise
                # Cleanup installed package on startup failure
                if installed.cleanup:
                    try:
                        installed.cleanup()
                    except Exception:  # noqa: BLE001 -- fault-barrier: cleanup failure must not mask startup error
                        pass
                raise

            tools = mcp_server.get_tool_names()

            metadata = LoadMetadata(
                loaded_at=datetime.now(),
                loaded_by=command.user_id,
                source=f"registry:{server.id}",
                verified=server.is_official,
                ephemeral=True,
                server_id=server.id,
                cleanup=installed.cleanup,
            )
            self._runtime_store.add(mcp_server, metadata)

            # Register tool access policy if specified
            if command.allow_tools or command.deny_tools:
                policy = ToolAccessPolicy(
                    allow_list=tuple(command.allow_tools or []),
                    deny_list=tuple(command.deny_tools or []),
                )
                resolver = get_tool_access_resolver()
                resolver.set_mcp_server_policy(mcp_server_id, policy)
                logger.debug(
                    "hot_loaded_mcp_server_tool_policy_set",
                    mcp_server_id=mcp_server_id,
                    has_allow_list=bool(command.allow_tools),
                    has_deny_list=bool(command.deny_tools),
                )

            duration_ms = (time.perf_counter() - start_time) * 1000

            self._event_bus.publish(
                McpServerHotLoaded(
                    mcp_server_id=mcp_server_id,
                    mcp_server_name=server.name,
                    source=f"registry:{server.id}",
                    verified=server.is_official,
                    user_id=command.user_id,
                    tools_count=len(tools),
                    load_duration_ms=duration_ms,
                )
            )

            return LoadResult(
                status="loaded",
                mcp_server_id=mcp_server_id,
                mcp_server_name=server.name,
                tools=[{"name": t} for t in tools],
                message=f"Successfully loaded '{server.name}' with {len(tools)} tools",
                warnings=warnings if warnings else None,
            )

        except (UnverifiedMcpServerError, MissingSecretsError):
            raise

        except Exception as e:  # noqa: BLE001 -- fault-barrier: catch-all for error event publishing, then re-raise
            # Redact secrets from error message if redactor exists
            error_reason = str(e)
            try:
                error_reason = redactor.redact(error_reason)
            except NameError:
                # Redactor not yet created (failed before secrets resolution)
                pass

            self._event_bus.publish(
                McpServerLoadFailed(
                    mcp_server_name=command.name,
                    reason=error_reason,
                    user_id=command.user_id,
                    error_type=type(e).__name__,
                )
            )
            raise

    async def _find_server(self, name: str) -> ServerDetails:
        """Find a server by name or ID.

        Args:
            name: Server name or ID.

        Returns:
            Server details.

        Raises:
            RegistryServerNotFoundError: If server not found.
            RegistryAmbiguousSearchError: If multiple servers match.
        """
        server = await self._registry_client.get_server(name)
        if server is not None:
            return server

        results = await self._registry_client.search(name, limit=5)
        if not results:
            raise RegistryServerNotFoundError(name)

        if len(results) == 1:
            server = await self._registry_client.get_server(results[0].id)
            if server is not None:
                return server
            raise RegistryServerNotFoundError(name)

        exact_match = next((r for r in results if r.id == name or r.name.lower() == name.lower()), None)
        if exact_match:
            server = await self._registry_client.get_server(exact_match.id)
            if server is not None:
                return server

        raise RegistryAmbiguousSearchError(name, [r.name for r in results])


class UnloadMcpServerHandler(CommandHandler):
    """Handler for UnloadMcpServerCommand.

    Unloads a hot-loaded mcp_server and cleans up resources.
    """

    def __init__(
        self,
        runtime_store: RuntimeMcpServerStore,
        event_bus: IEventBus,
    ):
        """Initialize the handler.

        Args:
            runtime_store: Store for hot-loaded mcp_servers.
            event_bus: Event bus for publishing events.
        """
        self._runtime_store = runtime_store
        self._event_bus = event_bus

    def handle(self, command: UnloadMcpServerCommand) -> dict[str, Any]:
        """Handle the unload mcp_server command.

        Args:
            command: The command to handle.

        Returns:
            Dictionary with unload result.

        Raises:
            McpServerNotHotLoadedError: If mcp_server is not hot-loaded.
        """
        entry = self._runtime_store.get(command.mcp_server_id)
        if entry is None:
            raise McpServerNotHotLoadedError(command.mcp_server_id)

        mcp_server, metadata = entry

        try:
            mcp_server.shutdown()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown failure must not prevent cleanup
            logger.warning(
                "mcp_server_shutdown_error",
                mcp_server_id=command.mcp_server_id,
                error=str(e),
            )

        if metadata.cleanup:
            try:
                metadata.cleanup()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: cleanup callback failure must not prevent unload
                logger.warning(
                    "mcp_server_cleanup_error",
                    mcp_server_id=command.mcp_server_id,
                    error=str(e),
                )

        self._runtime_store.remove(command.mcp_server_id)

        # Remove tool access policy for unloaded mcp_server
        resolver = get_tool_access_resolver()
        resolver.remove_mcp_server_policy(command.mcp_server_id)

        lifetime_seconds = metadata.lifetime_seconds()

        self._event_bus.publish(
            McpServerHotUnloaded(
                mcp_server_id=command.mcp_server_id,
                user_id=command.user_id,
                lifetime_seconds=lifetime_seconds,
            )
        )

        return {
            "unloaded": command.mcp_server_id,
            "lifetime_seconds": round(lifetime_seconds, 1),
        }
