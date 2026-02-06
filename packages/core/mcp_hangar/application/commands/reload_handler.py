"""Command handler for configuration reload."""

import time
from typing import Any

from ...domain.events import ConfigurationReloaded, ConfigurationReloadFailed, ConfigurationReloadRequested
from ...domain.exceptions import ConfigurationError
from ...domain.repository import IProviderRepository
from ...infrastructure.command_bus import CommandHandler
from ...infrastructure.event_bus import EventBus
from ...logging_config import get_logger
from ...server.config import load_config, load_config_from_file
from ...server.state import GROUPS
from .commands import ReloadConfigurationCommand

logger = get_logger(__name__)


class ReloadConfigurationHandler(CommandHandler):
    """Handler for ReloadConfigurationCommand.

    Reloads configuration from file and applies changes:
    - Adds new providers
    - Removes deleted providers
    - Updates modified providers (restart with new config)
    - Preserves unchanged providers (no restart)
    """

    def __init__(
        self,
        provider_repository: IProviderRepository,
        event_bus: EventBus,
        current_config_path: str | None = None,
    ):
        """Initialize the handler.

        Args:
            provider_repository: Repository for provider persistence.
            event_bus: Event bus for publishing events.
            current_config_path: Current configuration file path.
        """
        self._repository = provider_repository
        self._event_bus = event_bus
        self._current_config_path = current_config_path

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
            new_full_config = load_config_from_file(config_path)
            new_providers_config = new_full_config.get("providers", {})

            # Capture current state
            current_providers = dict(self._repository.get_all())
            # Note: Group reload not yet implemented (GROUPS state captured but not used)

            # Calculate diff
            new_ids = set(new_providers_config.keys())
            current_ids = set(current_providers.keys())

            added_ids = new_ids - current_ids
            removed_ids = current_ids - new_ids
            potentially_updated_ids = new_ids & current_ids

            # Check for actual configuration changes
            updated_ids = []
            unchanged_ids = []
            for provider_id in potentially_updated_ids:
                old_spec = self._get_provider_spec(current_providers[provider_id])
                new_spec = new_providers_config[provider_id]
                if self._config_differs(old_spec, new_spec):
                    updated_ids.append(provider_id)
                else:
                    unchanged_ids.append(provider_id)

            logger.info(
                "config_reload_diff_calculated",
                added=len(added_ids),
                removed=len(removed_ids),
                updated=len(updated_ids),
                unchanged=len(unchanged_ids),
            )

            # Apply changes atomically
            # 1. Stop removed and updated providers
            for provider_id in list(removed_ids) + updated_ids:
                provider = current_providers.get(provider_id)
                if provider:
                    try:
                        if command.graceful:
                            # Graceful shutdown with idle wait
                            provider.stop(reason="config_reload")
                        else:
                            # Immediate shutdown
                            provider.shutdown()

                        logger.info(
                            "provider_stopped_for_reload",
                            provider_id=provider_id,
                            graceful=command.graceful,
                        )
                    except Exception as e:
                        logger.warning(
                            "provider_stop_failed_during_reload",
                            provider_id=provider_id,
                            error=str(e),
                        )

            # 2. Remove deleted providers from repository
            for provider_id in removed_ids:
                if provider_id in current_providers:
                    self._repository.remove(provider_id)
                    logger.info("provider_removed", provider_id=provider_id)

            # 3. Clear groups (will be reloaded)
            GROUPS.clear()

            # 4. Load new configuration (adds new and updates existing)
            load_config(new_providers_config)

            # 5. Auto-start providers if they were running before
            # (This depends on auto_start config and provider state)
            for provider_id in added_ids:
                provider = self._repository.get(provider_id)
                if provider:
                    logger.info("provider_added", provider_id=provider_id)

            for provider_id in updated_ids:
                provider = self._repository.get(provider_id)
                if provider:
                    logger.info("provider_updated", provider_id=provider_id)

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Publish success event
            self._event_bus.publish(
                ConfigurationReloaded(
                    config_path=config_path,
                    providers_added=list(added_ids),
                    providers_removed=list(removed_ids),
                    providers_updated=updated_ids,
                    providers_unchanged=unchanged_ids,
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
                "providers_added": list(added_ids),
                "providers_removed": list(removed_ids),
                "providers_updated": updated_ids,
                "providers_unchanged": unchanged_ids,
                "duration_ms": duration_ms,
            }

        except Exception as e:
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

    def _get_provider_spec(self, provider) -> dict[str, Any]:
        """Extract configuration spec from provider aggregate.

        Args:
            provider: Provider aggregate instance.

        Returns:
            Dictionary with provider configuration.
        """
        return {
            "mode": provider._mode.value if hasattr(provider._mode, "value") else str(provider._mode),
            "command": provider._command,
            "image": provider._image,
            "endpoint": provider._endpoint,
            "env": provider._env,
            "idle_ttl_s": provider._idle_ttl.seconds if hasattr(provider._idle_ttl, "seconds") else provider._idle_ttl,
            "health_check_interval_s": (
                provider._health_check_interval.seconds if hasattr(provider._health_check_interval, "seconds") else 60
            ),
            "max_consecutive_failures": (
                provider._health.max_consecutive_failures
                if hasattr(provider._health, "max_consecutive_failures")
                else 3
            ),
            "volumes": provider._volumes,
            "build": provider._build,
            "resources": provider._resources,
            "network": provider._network,
            "read_only": provider._read_only,
            "user": provider._user,
            "description": provider._description,
            "tools": provider._tools.to_dict() if hasattr(provider._tools, "to_dict") else None,
        }

    def _config_differs(self, old_spec: dict[str, Any], new_spec: dict[str, Any]) -> bool:
        """Check if two provider configurations differ significantly.

        Args:
            old_spec: Old provider configuration.
            new_spec: New provider configuration.

        Returns:
            True if configurations differ, False otherwise.
        """
        # Default values for provider fields
        DEFAULTS = {
            "idle_ttl_s": 300,
            "health_check_interval_s": 60,
            "max_consecutive_failures": 3,
            "network": "none",
            "read_only": True,
        }

        # Compare key fields that affect provider behavior
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
