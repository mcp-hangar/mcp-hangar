"""Command handler for configuration reload."""

import time
from typing import Any

from ...domain.contracts.command import CommandHandler
from ...domain.contracts.event_bus import IEventBus
from ...domain.events import ConfigurationReloaded, ConfigurationReloadFailed, ConfigurationReloadRequested
from ...domain.exceptions import ConfigurationError
from ...domain.repository import IMcpServerRepository
from ...domain.services import get_tool_access_resolver
from ...logging_config import get_logger
from ..ports.config_loader import IConfigLoader
from .commands import ReloadConfigurationCommand

logger = get_logger(__name__)


class ReloadConfigurationHandler(CommandHandler):
    """Handler for ReloadConfigurationCommand.

    Reloads configuration from file and applies changes:
    - Adds new mcp_servers
    - Removes deleted mcp_servers
    - Updates modified mcp_servers (restart with new config)
    - Preserves unchanged mcp_servers (no restart)
    """

    def __init__(
        self,
        mcp_server_repository: IMcpServerRepository,
        event_bus: IEventBus,
        current_config_path: str | None = None,
        config_loader: IConfigLoader | None = None,
        groups: dict | None = None,
    ):
        """Initialize the handler.

        Args:
            mcp_server_repository: Repository for mcp_server persistence.
            event_bus: Event bus for publishing events.
            current_config_path: Current configuration file path.
            config_loader: Config loader for loading/applying configuration.
            groups: Groups dict reference for clearing during reload.
        """
        self._repository = mcp_server_repository
        self._event_bus = event_bus
        self._current_config_path = current_config_path
        self._config_loader = config_loader
        self._groups = groups if groups is not None else {}

    def handle(self, command: ReloadConfigurationCommand) -> dict[str, Any]:
        """Handle the reload configuration command.

        Args:
            command: The command to handle.

        Returns:
            Dictionary with reload results.

        Raises:
            ConfigurationError: If configuration is invalid or cannot be loaded.
        """
        start_time = time.perf_counter()

        # Determine config path
        config_path = command.config_path or self._current_config_path
        if not config_path:
            raise ConfigurationError("No configuration path specified")

        # Publish reload requested event
        self._event_bus.publish(
            ConfigurationReloadRequested(
                config_path=config_path,
                requested_by=command.requested_by,
                force=not command.graceful,
            )
        )

        try:
            # Load and validate new configuration
            if self._config_loader is not None:
                new_full_config = self._config_loader.load_from_file(config_path)
            else:
                # Fallback: import server-layer function directly (legacy path)
                from ...server.config import load_config_from_file as _load_from_file

                new_full_config = _load_from_file(config_path)
            new_mcp_servers_config = new_full_config.get("mcp_servers", {})

            # Capture current state
            current_mcp_servers = dict(self._repository.get_all())
            # Note: Group reload not yet implemented (GROUPS state captured but not used)

            # Calculate diff
            new_ids = set(new_mcp_servers_config.keys())
            current_ids = set(current_mcp_servers.keys())

            added_ids = new_ids - current_ids
            removed_ids = current_ids - new_ids
            potentially_updated_ids = new_ids & current_ids

            # Check for actual configuration changes
            updated_ids = []
            unchanged_ids = []
            for mcp_server_id in potentially_updated_ids:
                old_spec = self._get_mcp_server_spec(current_mcp_servers[mcp_server_id])
                new_spec = new_mcp_servers_config[mcp_server_id]
                if self._config_differs(old_spec, new_spec):
                    updated_ids.append(mcp_server_id)
                else:
                    unchanged_ids.append(mcp_server_id)

            logger.info(
                "config_reload_diff_calculated",
                added=len(added_ids),
                removed=len(removed_ids),
                updated=len(updated_ids),
                unchanged=len(unchanged_ids),
            )

            # Apply changes atomically
            # 1. Stop removed and updated mcp_servers
            for mcp_server_id in list(removed_ids) + updated_ids:
                mcp_server = current_mcp_servers.get(mcp_server_id)
                if mcp_server:
                    try:
                        if command.graceful:
                            # Graceful shutdown with idle wait
                            mcp_server.stop(reason="config_reload")
                        else:
                            # Immediate shutdown
                            mcp_server.shutdown()

                        logger.info(
                            "mcp_server_stopped_for_reload",
                            mcp_server_id=mcp_server_id,
                            graceful=command.graceful,
                        )
                    except Exception as e:  # noqa: BLE001 -- fault-barrier: stop failure must not prevent reload of other mcp_servers
                        logger.warning(
                            "mcp_server_stop_failed_during_reload",
                            mcp_server_id=mcp_server_id,
                            error=str(e),
                        )

            # 2. Remove deleted mcp_servers from repository
            for mcp_server_id in removed_ids:
                if mcp_server_id in current_mcp_servers:
                    self._repository.remove(mcp_server_id)
                    logger.info("mcp_server_removed", mcp_server_id=mcp_server_id)

            # 3. Clear groups (will be reloaded)
            self._groups.clear()

            # 4. Invalidate and clear tool access policies before reload
            # Policies will be re-registered during load_config
            resolver = get_tool_access_resolver()
            resolver.clear_all()
            logger.debug("tool_access_policies_cleared_for_reload")

            # 5. Load new configuration (adds new and updates existing)
            if self._config_loader is not None:
                self._config_loader.apply_mcp_servers(new_mcp_servers_config)
            else:
                # Fallback: import server-layer function directly (legacy path)
                from ...server.config import load_config as _load_config

                _load_config(new_mcp_servers_config)

            # 6. Auto-start mcp_servers if they were running before
            # (This depends on auto_start config and mcp_server state)
            for mcp_server_id in added_ids:
                mcp_server = self._repository.get(mcp_server_id)
                if mcp_server:
                    logger.info("mcp_server_added", mcp_server_id=mcp_server_id)

            for mcp_server_id in updated_ids:
                mcp_server = self._repository.get(mcp_server_id)
                if mcp_server:
                    logger.info("mcp_server_updated", mcp_server_id=mcp_server_id)

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Publish success event
            self._event_bus.publish(
                ConfigurationReloaded(
                    config_path=config_path,
                    mcp_servers_added=list(added_ids),
                    mcp_servers_removed=list(removed_ids),
                    mcp_servers_updated=updated_ids,
                    mcp_servers_unchanged=unchanged_ids,
                    reload_duration_ms=duration_ms,
                    requested_by=command.requested_by,
                )
            )

            logger.info(
                "configuration_reloaded",
                config_path=config_path,
                duration_ms=duration_ms,
                added=len(added_ids),
                removed=len(removed_ids),
                updated=len(updated_ids),
            )

            return {
                "success": True,
                "config_path": config_path,
                "mcp_servers_added": list(added_ids),
                "mcp_servers_removed": list(removed_ids),
                "mcp_servers_updated": updated_ids,
                "mcp_servers_unchanged": unchanged_ids,
                "duration_ms": duration_ms,
            }

        except Exception as e:  # noqa: BLE001 -- fault-barrier: wrap reload errors in ConfigurationError for callers
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Publish failure event
            self._event_bus.publish(
                ConfigurationReloadFailed(
                    config_path=config_path,
                    reason=str(e),
                    error_type=type(e).__name__,
                    requested_by=command.requested_by,
                )
            )

            logger.error(
                "configuration_reload_failed",
                config_path=config_path,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )

            raise ConfigurationError(f"Configuration reload failed: {e}") from e

    def _get_mcp_server_spec(self, mcp_server) -> dict[str, Any]:
        """Extract configuration spec from mcp_server aggregate.

        Args:
            mcp_server: McpServer aggregate instance.

        Returns:
            Dictionary with mcp_server configuration.
        """
        return {
            "mode": mcp_server._mode.value if hasattr(mcp_server._mode, "value") else str(mcp_server._mode),
            "command": mcp_server._command,
            "image": mcp_server._image,
            "endpoint": mcp_server._endpoint,
            "env": mcp_server._env,
            "idle_ttl_s": mcp_server._idle_ttl.seconds
            if hasattr(mcp_server._idle_ttl, "seconds")
            else mcp_server._idle_ttl,
            "health_check_interval_s": (
                mcp_server._health_check_interval.seconds
                if hasattr(mcp_server._health_check_interval, "seconds")
                else 60
            ),
            "max_consecutive_failures": (
                mcp_server._health.max_consecutive_failures
                if hasattr(mcp_server._health, "max_consecutive_failures")
                else 3
            ),
            "volumes": mcp_server._volumes,
            "build": mcp_server._build,
            "resources": mcp_server._resources,
            "network": mcp_server._network,
            "read_only": mcp_server._read_only,
            "user": mcp_server._user,
            "description": mcp_server._description,
            "tools": mcp_server._tools.to_dict() if hasattr(mcp_server._tools, "to_dict") else None,
        }

    def _config_differs(self, old_spec: dict[str, Any], new_spec: dict[str, Any]) -> bool:
        """Check if two mcp_server configurations differ significantly.

        Args:
            old_spec: Old mcp_server configuration.
            new_spec: New mcp_server configuration.

        Returns:
            True if configurations differ, False otherwise.
        """
        # Default values for mcp_server fields
        DEFAULTS = {
            "idle_ttl_s": 300,
            "health_check_interval_s": 60,
            "max_consecutive_failures": 3,
            "network": "none",
            "read_only": True,
        }

        # Compare key fields that affect mcp_server behavior
        key_fields = [
            "mode",
            "command",
            "image",
            "endpoint",
            "env",
            "idle_ttl_s",
            "health_check_interval_s",
            "max_consecutive_failures",
            "volumes",
            "build",
            "resources",
            "network",
            "user",
        ]

        for field in key_fields:
            old_value = old_spec.get(field)
            new_value = new_spec.get(field)

            # Normalize empty values for env (None, {}, etc.)
            if field in ("env", "resources"):
                old_value = old_value or {}
                new_value = new_value or {}

            # Normalize empty lists/None
            if field in ("volumes", "command"):
                old_value = old_value or []
                new_value = new_value or []

            # Normalize default values - None in new_spec means use default
            if field in DEFAULTS:
                if new_value is None:
                    new_value = DEFAULTS[field]
                if old_value is None:
                    old_value = DEFAULTS[field]

            if old_value != new_value:
                logger.debug(
                    "config_field_differs",
                    field=field,
                    old=old_value,
                    new=new_value,
                )
                return True

        return False
