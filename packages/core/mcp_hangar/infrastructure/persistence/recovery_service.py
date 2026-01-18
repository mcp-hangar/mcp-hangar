"""Recovery service for system startup.

Responsible for loading persisted provider configurations and
restoring system state after restart.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ...domain.contracts.persistence import AuditAction, AuditEntry, ProviderConfigSnapshot
from ...domain.model import Provider
from ...domain.repository import IProviderRepository
from ...logging_config import get_logger
from .audit_repository import SQLiteAuditRepository
from .config_repository import SQLiteProviderConfigRepository
from .database import Database

logger = get_logger(__name__)


@dataclass
class RecoveryResult:
    """Result of a recovery operation."""

    recovered_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    recovered_ids: list[str] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    duration_ms: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RecoveryService:
    """Service for recovering system state on startup.

    Loads persisted provider configurations from the database
    and registers them with the provider repository.
    """

    def __init__(
        self,
        database: Database,
        provider_repository: IProviderRepository,
        config_repository: SQLiteProviderConfigRepository | None = None,
        audit_repository: SQLiteAuditRepository | None = None,
        auto_start: bool = False,
    ):
        """Initialize recovery service.

        Args:
            database: Database instance
            provider_repository: Repository for registering recovered providers
            config_repository: Optional config repository (created if not provided)
            audit_repository: Optional audit repository for logging recovery
            auto_start: Whether to auto-start recovered providers
        """
        self._db = database
        self._provider_repo = provider_repository
        self._config_repo = config_repository or SQLiteProviderConfigRepository(database)
        self._audit_repo = audit_repository or SQLiteAuditRepository(database)
        self._auto_start = auto_start
        self._last_recovery: RecoveryResult | None = None

    async def recover_providers(self) -> list[str]:
        """Recover all provider configurations from storage.

        Loads saved configurations and registers Provider aggregates
        with the provider repository.

        Returns:
            List of recovered provider IDs
        """
        result = RecoveryResult(started_at=datetime.now(UTC))
        start_time = datetime.now(UTC)

        try:
            # Ensure database is initialized
            await self._db.initialize()

            # Load all enabled configurations
            configs = await self._config_repo.get_all()

            logger.info(f"Recovery: Found {len(configs)} provider configurations")

            for config in configs:
                try:
                    # Create Provider aggregate from config
                    provider = self._create_provider_from_config(config)

                    # Register with repository
                    self._provider_repo.add(config.provider_id, provider)

                    result.recovered_count += 1
                    result.recovered_ids.append(config.provider_id)

                    logger.debug(f"Recovery: Restored provider {config.provider_id}")

                except Exception as e:
                    result.failed_count += 1
                    result.failed_ids.append(config.provider_id)
                    result.errors[config.provider_id] = str(e)
                    logger.error(f"Recovery: Failed to restore provider {config.provider_id}: {e}")

            # Record recovery in audit log
            await self._record_recovery_audit(result)

        except Exception as e:
            logger.error(f"Recovery: Critical failure: {e}")
            result.errors["_critical"] = str(e)

        finally:
            result.completed_at = datetime.now(UTC)
            result.duration_ms = (result.completed_at - start_time).total_seconds() * 1000
            self._last_recovery = result

        logger.info(
            f"Recovery completed: {result.recovered_count} recovered, "
            f"{result.failed_count} failed, {result.duration_ms:.2f}ms"
        )

        return result.recovered_ids

    def _create_provider_from_config(self, config: ProviderConfigSnapshot) -> Provider:
        """Create Provider aggregate from configuration snapshot.

        Args:
            config: Provider configuration snapshot

        Returns:
            Provider aggregate instance
        """
        return Provider(
            provider_id=config.provider_id,
            mode=config.mode,
            command=config.command,
            image=config.image,
            endpoint=config.endpoint,
            env=config.env,
            idle_ttl_s=config.idle_ttl_s,
            health_check_interval_s=config.health_check_interval_s,
            max_consecutive_failures=config.max_consecutive_failures,
            description=config.description,
            volumes=config.volumes,
            build=config.build,
            resources=config.resources,
            network=config.network,
            read_only=config.read_only,
            user=config.user,
            tools=config.tools,
        )

    async def _record_recovery_audit(self, result: RecoveryResult) -> None:
        """Record recovery operation in audit log.

        Args:
            result: Recovery result to record
        """
        try:
            await self._audit_repo.append(
                AuditEntry(
                    entity_id="_system",
                    entity_type="recovery",
                    action=AuditAction.RECOVERED,
                    timestamp=result.completed_at or datetime.now(UTC),
                    actor="system",
                    metadata={
                        "recovered_count": result.recovered_count,
                        "failed_count": result.failed_count,
                        "duration_ms": result.duration_ms,
                        "recovered_ids": result.recovered_ids,
                        "failed_ids": result.failed_ids,
                        "errors": result.errors,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Failed to record recovery audit: {e}")

    async def get_recovery_status(self) -> dict[str, Any]:
        """Get status of last recovery operation.

        Returns:
            Dictionary with recovery metrics and status
        """
        if self._last_recovery is None:
            return {
                "status": "not_run",
                "message": "No recovery has been performed",
            }

        result = self._last_recovery

        return {
            "status": "completed" if not result.errors else "completed_with_errors",
            "recovered_count": result.recovered_count,
            "failed_count": result.failed_count,
            "skipped_count": result.skipped_count,
            "duration_ms": result.duration_ms,
            "started_at": result.started_at.isoformat() if result.started_at else None,
            "completed_at": (result.completed_at.isoformat() if result.completed_at else None),
            "recovered_ids": result.recovered_ids,
            "failed_ids": result.failed_ids,
            "errors": result.errors,
        }

    async def recover_single_provider(self, provider_id: str) -> bool:
        """Recover a single provider from storage.

        Useful for re-loading a specific provider without full recovery.

        Args:
            provider_id: Provider identifier to recover

        Returns:
            True if recovered successfully, False otherwise
        """
        try:
            config = await self._config_repo.get(provider_id)

            if config is None:
                logger.warning(f"Recovery: No config found for {provider_id}")
                return False

            provider = self._create_provider_from_config(config)
            self._provider_repo.add(provider_id, provider)

            logger.info(f"Recovery: Single provider {provider_id} restored")
            return True

        except Exception as e:
            logger.error(f"Recovery: Failed to restore {provider_id}: {e}")
            return False

    async def save_provider_config(self, provider: Provider) -> None:
        """Save a provider's configuration to persistent storage.

        Creates a snapshot of the current provider configuration
        and persists it for future recovery.

        Args:
            provider: Provider to save configuration for
        """
        config = ProviderConfigSnapshot(
            provider_id=provider.provider_id,
            mode=provider.mode_str,
            command=provider._command,
            image=provider._image,
            endpoint=provider._endpoint,
            env=provider._env,
            idle_ttl_s=provider._idle_ttl.seconds,
            health_check_interval_s=provider._health_check_interval.seconds,
            max_consecutive_failures=provider._health.max_consecutive_failures,
            description=provider.description,
            volumes=provider._volumes,
            build=provider._build,
            resources=provider._resources,
            network=provider._network,
            read_only=provider._read_only,
            user=provider._user,
            tools=([t.to_dict() for t in provider.tools] if provider._tools_predefined else None),
            enabled=True,
        )

        await self._config_repo.save(config)

        # Record in audit log
        await self._audit_repo.append(
            AuditEntry(
                entity_id=provider.provider_id,
                entity_type="provider",
                action=AuditAction.UPDATED,
                timestamp=datetime.now(UTC),
                actor="system",
                new_state=config.to_dict(),
            )
        )

        logger.debug(f"Saved config for provider: {provider.provider_id}")

    async def delete_provider_config(self, provider_id: str) -> bool:
        """Delete a provider's configuration from storage.

        Soft-deletes the configuration (marks as disabled).

        Args:
            provider_id: Provider identifier

        Returns:
            True if deleted, False if not found
        """
        # Get current config for audit
        old_config = await self._config_repo.get(provider_id)

        deleted = await self._config_repo.delete(provider_id)

        if deleted and old_config:
            await self._audit_repo.append(
                AuditEntry(
                    entity_id=provider_id,
                    entity_type="provider",
                    action=AuditAction.DELETED,
                    timestamp=datetime.now(UTC),
                    actor="system",
                    old_state=old_config.to_dict(),
                )
            )

        return deleted
