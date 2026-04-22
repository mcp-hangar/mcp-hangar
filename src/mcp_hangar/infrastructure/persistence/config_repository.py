"""McpServer configuration repository implementations.

Provides both in-memory and SQLite implementations of IMcpServerConfigRepository.
"""

from datetime import datetime, UTC
import json
import threading

from ...domain.contracts.persistence import ConcurrentModificationError, PersistenceError, McpServerConfigSnapshot
from ...logging_config import get_logger
from .database import Database

logger = get_logger(__name__)


class InMemoryMcpServerConfigRepository:
    """In-memory implementation of mcp_server config repository.

    Useful for testing and development. Data is lost on restart.
    Thread-safe implementation.
    """

    def __init__(self):
        """Initialize empty in-memory repository."""
        self._configs: dict[str, McpServerConfigSnapshot] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.RLock()

    async def save(self, config: McpServerConfigSnapshot) -> None:
        """Save mcp_server configuration."""
        with self._lock:
            now = datetime.now(UTC)

            # Update timestamps
            if config.mcp_server_id in self._configs:
                # Update existing
                new_config = McpServerConfigSnapshot(
                    **{
                        **config.to_dict(),
                        "created_at": self._configs[config.mcp_server_id].created_at,
                        "updated_at": now,
                    }
                )
                self._versions[config.mcp_server_id] = self._versions.get(config.mcp_server_id, 0) + 1
            else:
                # Create new
                new_config = McpServerConfigSnapshot(
                    **{
                        **config.to_dict(),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                self._versions[config.mcp_server_id] = 1

            self._configs[config.mcp_server_id] = new_config
            logger.debug(f"Saved config for mcp_server: {config.mcp_server_id}")

    async def get(self, mcp_server_id: str) -> McpServerConfigSnapshot | None:
        """Retrieve mcp_server configuration by ID."""
        with self._lock:
            return self._configs.get(mcp_server_id)

    async def get_all(self) -> list[McpServerConfigSnapshot]:
        """Retrieve all mcp_server configurations."""
        with self._lock:
            return list(self._configs.values())

    async def delete(self, mcp_server_id: str) -> bool:
        """Delete mcp_server configuration."""
        with self._lock:
            if mcp_server_id in self._configs:
                del self._configs[mcp_server_id]
                self._versions.pop(mcp_server_id, None)
                logger.debug(f"Deleted config for mcp_server: {mcp_server_id}")
                return True
            return False

    async def exists(self, mcp_server_id: str) -> bool:
        """Check if mcp_server configuration exists."""
        with self._lock:
            return mcp_server_id in self._configs

    def clear(self) -> None:
        """Clear all configurations (for testing)."""
        with self._lock:
            self._configs.clear()
            self._versions.clear()


class SQLiteMcpServerConfigRepository:
    """SQLite implementation of mcp_server config repository.

    Provides durable storage with optimistic concurrency control.
    """

    def __init__(self, database: Database):
        """Initialize with database connection.

        Args:
            database: Database instance for connections
        """
        self._db = database

    async def save(self, config: McpServerConfigSnapshot) -> None:
        """Save mcp_server configuration with optimistic locking.

        Args:
            config: McpServer configuration to save

        Raises:
            ConcurrentModificationError: If version conflict detected
            PersistenceError: If save operation fails
        """
        try:
            async with self._db.transaction() as conn:
                # Check existing version
                cursor = await conn.execute(
                    "SELECT version FROM mcp_server_configs WHERE mcp_server_id = ?",
                    (config.mcp_server_id,),
                )
                row = await cursor.fetchone()

                config_json = json.dumps(config.to_dict())
                now = datetime.now(UTC).isoformat()

                if row is None:
                    # Insert new config
                    await conn.execute(
                        """
                        INSERT INTO mcp_server_configs
                        (mcp_server_id, mode, config_json, enabled, version, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            config.mcp_server_id,
                            config.mode,
                            config_json,
                            1 if config.enabled else 0,
                            now,
                            now,
                        ),
                    )
                    logger.debug(f"Inserted new config for mcp_server: {config.mcp_server_id}")
                else:
                    # Update existing config with version increment
                    current_version = row[0]
                    new_version = current_version + 1

                    result = await conn.execute(
                        """
                        UPDATE mcp_server_configs
                        SET mode = ?, config_json = ?, enabled = ?,
                            version = ?, updated_at = ?
                        WHERE mcp_server_id = ? AND version = ?
                        """,
                        (
                            config.mode,
                            config_json,
                            1 if config.enabled else 0,
                            new_version,
                            now,
                            config.mcp_server_id,
                            current_version,
                        ),
                    )

                    if result.rowcount == 0:
                        raise ConcurrentModificationError(
                            config.mcp_server_id,
                            current_version,
                            current_version + 1,
                        )

                    logger.debug(
                        f"Updated config for mcp_server: {config.mcp_server_id} "
                        f"(version {current_version} -> {new_version})"
                    )

        except ConcurrentModificationError:
            raise
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to save mcp_server config: {e}")
            raise PersistenceError(f"Failed to save mcp_server config: {e}") from e

    async def get(self, mcp_server_id: str) -> McpServerConfigSnapshot | None:
        """Retrieve mcp_server configuration by ID.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            Configuration snapshot if found, None otherwise
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT config_json FROM mcp_server_configs WHERE mcp_server_id = ?",
                    (mcp_server_id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    return None

                config_data = json.loads(row[0])
                return McpServerConfigSnapshot.from_dict(config_data)

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to get mcp_server config: {e}")
            raise PersistenceError(f"Failed to get mcp_server config: {e}") from e

    async def get_all(self) -> list[McpServerConfigSnapshot]:
        """Retrieve all mcp_server configurations.

        Returns:
            List of all stored configurations
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute("SELECT config_json FROM mcp_server_configs WHERE enabled = 1")
                rows = await cursor.fetchall()

                configs = []
                for row in rows:
                    try:
                        config_data = json.loads(row[0])
                        configs.append(McpServerConfigSnapshot.from_dict(config_data))
                    except Exception as e:  # noqa: BLE001 -- infra-boundary: skip malformed config entry
                        logger.warning(f"Failed to deserialize config: {e}")
                        continue

                return configs

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to get all mcp_server configs: {e}")
            raise PersistenceError(f"Failed to get all mcp_server configs: {e}") from e

    async def delete(self, mcp_server_id: str) -> bool:
        """Delete mcp_server configuration (soft delete by disabling).

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            True if deleted, False if not found
        """
        try:
            async with self._db.transaction() as conn:
                # Soft delete - mark as disabled
                result = await conn.execute(
                    """
                    UPDATE mcp_server_configs
                    SET enabled = 0, updated_at = ?
                    WHERE mcp_server_id = ? AND enabled = 1
                    """,
                    (datetime.now(UTC).isoformat(), mcp_server_id),
                )

                deleted = result.rowcount > 0
                if deleted:
                    logger.debug(f"Soft-deleted config for mcp_server: {mcp_server_id}")

                return deleted

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to delete mcp_server config: {e}")
            raise PersistenceError(f"Failed to delete mcp_server config: {e}") from e

    async def hard_delete(self, mcp_server_id: str) -> bool:
        """Permanently delete mcp_server configuration.

        Use with caution - this removes all history.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            True if deleted, False if not found
        """
        try:
            async with self._db.transaction() as conn:
                result = await conn.execute(
                    "DELETE FROM mcp_server_configs WHERE mcp_server_id = ?",
                    (mcp_server_id,),
                )

                deleted = result.rowcount > 0
                if deleted:
                    logger.info(f"Hard-deleted config for mcp_server: {mcp_server_id}")

                return deleted

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to hard-delete mcp_server config: {e}")
            raise PersistenceError(f"Failed to hard-delete mcp_server config: {e}") from e

    async def exists(self, mcp_server_id: str) -> bool:
        """Check if mcp_server configuration exists.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            True if exists and enabled, False otherwise
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT 1 FROM mcp_server_configs WHERE mcp_server_id = ? AND enabled = 1",
                    (mcp_server_id,),
                )
                row = await cursor.fetchone()
                return row is not None

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to check mcp_server existence: {e}")
            raise PersistenceError(f"Failed to check mcp_server existence: {e}") from e

    async def get_with_version(self, mcp_server_id: str) -> tuple[McpServerConfigSnapshot, int] | None:
        """Get configuration with its version for optimistic locking.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            Tuple of (config, version) if found, None otherwise
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT config_json, version FROM mcp_server_configs WHERE mcp_server_id = ?",
                    (mcp_server_id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    return None

                config_data = json.loads(row[0])
                return (McpServerConfigSnapshot.from_dict(config_data), row[1])

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to get mcp_server config with version: {e}")
            raise PersistenceError(f"Failed to get mcp_server config with version: {e}") from e

    async def update_last_started(self, mcp_server_id: str) -> None:
        """Update the last_started_at timestamp.

        Args:
            mcp_server_id: McpServer identifier
        """
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    """
                    UPDATE mcp_server_configs
                    SET last_started_at = ?, updated_at = ?
                    WHERE mcp_server_id = ?
                    """,
                    (
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                        mcp_server_id,
                    ),
                )

        except Exception as e:  # noqa: BLE001 -- infra-boundary: non-critical, best-effort update
            logger.error(f"Failed to update last_started_at: {e}")
            # Non-critical operation, don't raise

    async def update_failure_count(self, mcp_server_id: str, consecutive_failures: int) -> None:
        """Update the consecutive failure count.

        Args:
            mcp_server_id: McpServer identifier
            consecutive_failures: Current failure count
        """
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    """
                    UPDATE mcp_server_configs
                    SET consecutive_failures = ?, updated_at = ?
                    WHERE mcp_server_id = ?
                    """,
                    (
                        consecutive_failures,
                        datetime.now(UTC).isoformat(),
                        mcp_server_id,
                    ),
                )

        except Exception as e:  # noqa: BLE001 -- infra-boundary: non-critical, best-effort update
            logger.error(f"Failed to update failure count: {e}")
            # Non-critical operation, don't raise
