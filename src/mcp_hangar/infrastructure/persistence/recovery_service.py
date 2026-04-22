"""Recovery service for system startup.

Responsible for loading persisted mcp_server configurations and
restoring system state after restart.
"""

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from ...domain.contracts.persistence import AuditAction, AuditEntry, McpServerConfigSnapshot
from ...domain.model import McpServer
from ...domain.repository import IMcpServerRepository
from ...logging_config import get_logger
from .audit_repository import SQLiteAuditRepository
from .config_repository import SQLiteMcpServerConfigRepository
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

    Loads persisted mcp_server configurations from the database
    and registers them with the mcp_server repository.
    """

    def __init__(
        self,
        database: Database,
        mcp_server_repository: IMcpServerRepository,
        config_repository: SQLiteMcpServerConfigRepository | None = None,
        audit_repository: SQLiteAuditRepository | None = None,
        auto_start: bool = False,
    ):
        """Initialize recovery service.

        Args:
            database: Database instance
            mcp_server_repository: Repository for registering recovered mcp_servers
            config_repository: Optional config repository (created if not provided)
            audit_repository: Optional audit repository for logging recovery
            auto_start: Whether to auto-start recovered mcp_servers
        """
        self._db = database
        self._mcp_server_repo = mcp_server_repository
        self._config_repo = config_repository or SQLiteMcpServerConfigRepository(database)
        self._audit_repo = audit_repository or SQLiteAuditRepository(database)
        self._auto_start = auto_start
        self._last_recovery: RecoveryResult | None = None

    async def recover_mcp_servers(self) -> list[str]:
        """Recover all mcp_server configurations from storage.

        Loads saved configurations and registers McpServer aggregates
        with the mcp_server repository.

        Returns:
            List of recovered mcp_server IDs
        """
        result = RecoveryResult(started_at=datetime.now(UTC))
        start_time = datetime.now(UTC)

        try:
            # Ensure database is initialized
            await self._db.initialize()

            # Load all enabled configurations
            configs = await self._config_repo.get_all()

            logger.info(f"Recovery: Found {len(configs)} mcp_server configurations")

            for config in configs:
                try:
                    # Create McpServer aggregate from config
                    mcp_server = self._create_mcp_server_from_config(config)

                    # Register with repository
                    self._mcp_server_repo.add(config.mcp_server_id, mcp_server)

                    result.recovered_count += 1
                    result.recovered_ids.append(config.mcp_server_id)

                    logger.debug(f"Recovery: Restored mcp_server {config.mcp_server_id}")

                except Exception as e:  # noqa: BLE001 -- infra-boundary: skip individual mcp_server on recovery failure
                    result.failed_count += 1
                    result.failed_ids.append(config.mcp_server_id)
                    result.errors[config.mcp_server_id] = str(e)
                    logger.error(f"Recovery: Failed to restore mcp_server {config.mcp_server_id}: {e}")

            # Record recovery in audit log
            await self._record_recovery_audit(result)

        except Exception as e:  # noqa: BLE001 -- infra-boundary: log critical recovery failure
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

    def _create_mcp_server_from_config(self, config: McpServerConfigSnapshot) -> McpServer:
        """Create McpServer aggregate from configuration snapshot.

        Args:
            config: McpServer configuration snapshot

        Returns:
            McpServer aggregate instance
        """
        return McpServer(
            mcp_server_id=config.mcp_server_id,
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
        except Exception as e:  # noqa: BLE001 -- infra-boundary: audit recording is non-critical
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

    async def recover_single_mcp_server(self, mcp_server_id: str) -> bool:
        """Recover a single mcp_server from storage.

        Useful for re-loading a specific mcp_server without full recovery.

        Args:
            mcp_server_id: McpServer identifier to recover

        Returns:
            True if recovered successfully, False otherwise
        """
        try:
            config = await self._config_repo.get(mcp_server_id)

            if config is None:
                logger.warning(f"Recovery: No config found for {mcp_server_id}")
                return False

            mcp_server = self._create_mcp_server_from_config(config)
            self._mcp_server_repo.add(mcp_server_id, mcp_server)

            logger.info(f"Recovery: Single mcp_server {mcp_server_id} restored")
            return True

        except Exception as e:  # noqa: BLE001 -- infra-boundary: returns False on recovery failure
            logger.error(f"Recovery: Failed to restore {mcp_server_id}: {e}")
            return False

    async def save_mcp_server_config(self, mcp_server: McpServer) -> None:
        """Save a mcp_server's configuration to persistent storage.

        Creates a snapshot of the current mcp_server configuration
        and persists it for future recovery.

        Args:
            mcp_server: McpServer to save configuration for
        """
        config = McpServerConfigSnapshot(
            mcp_server_id=mcp_server.mcp_server_id,
            mode=mcp_server.mode_str,
            command=mcp_server._command,
            image=mcp_server._image,
            endpoint=mcp_server._endpoint,
            env=mcp_server._env,
            idle_ttl_s=mcp_server._idle_ttl.seconds,
            health_check_interval_s=mcp_server._health_check_interval.seconds,
            max_consecutive_failures=mcp_server._health.max_consecutive_failures,
            description=mcp_server.description,
            volumes=mcp_server._volumes,
            build=mcp_server._build,
            resources=mcp_server._resources,
            network=mcp_server._network,
            read_only=mcp_server._read_only,
            user=mcp_server._user,
            tools=([t.to_dict() for t in mcp_server.tools] if mcp_server._tools_predefined else None),
            enabled=True,
        )

        await self._config_repo.save(config)

        # Record in audit log
        await self._audit_repo.append(
            AuditEntry(
                entity_id=mcp_server.mcp_server_id,
                entity_type="mcp_server",
                action=AuditAction.UPDATED,
                timestamp=datetime.now(UTC),
                actor="system",
                new_state=config.to_dict(),
            )
        )

        logger.debug(f"Saved config for mcp_server: {mcp_server.mcp_server_id}")

    async def delete_mcp_server_config(self, mcp_server_id: str) -> bool:
        """Delete a mcp_server's configuration from storage.

        Soft-deletes the configuration (marks as disabled).

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            True if deleted, False if not found
        """
        # Get current config for audit
        old_config = await self._config_repo.get(mcp_server_id)

        deleted = await self._config_repo.delete(mcp_server_id)

        if deleted and old_config:
            await self._audit_repo.append(
                AuditEntry(
                    entity_id=mcp_server_id,
                    entity_type="mcp_server",
                    action=AuditAction.DELETED,
                    timestamp=datetime.now(UTC),
                    actor="system",
                    old_state=old_config.to_dict(),
                )
            )

        return deleted
