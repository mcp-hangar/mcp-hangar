"""Provider configuration repository implementations.

Provides both in-memory and SQLite implementations of IProviderConfigRepository.
"""

from datetime import datetime, timezone
import json
import threading
from typing import Dict, List, Optional

from ...domain.contracts.persistence import ConcurrentModificationError, PersistenceError, ProviderConfigSnapshot
from ...logging_config import get_logger
from .database import Database

logger = get_logger(__name__)


class InMemoryProviderConfigRepository:
    """In-memory implementation of provider config repository.

    Useful for testing and development. Data is lost on restart.
    Thread-safe implementation.
    """

    def __init__(self):
        """Initialize empty in-memory repository."""
        self._configs: Dict[str, ProviderConfigSnapshot] = {}
        self._versions: Dict[str, int] = {}
        self._lock = threading.RLock()

    async def save(self, config: ProviderConfigSnapshot) -> None:
        """Save provider configuration."""
        with self._lock:
            now = datetime.now(timezone.utc)

            # Update timestamps
            if config.provider_id in self._configs:
                # Update existing
                new_config = ProviderConfigSnapshot(
                    **{
                        **config.to_dict(),
                        "created_at": self._configs[config.provider_id].created_at,
                        "updated_at": now,
                    }
                )
                self._versions[config.provider_id] = self._versions.get(config.provider_id, 0) + 1
            else:
                # Create new
                new_config = ProviderConfigSnapshot(
                    **{
                        **config.to_dict(),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                self._versions[config.provider_id] = 1

            self._configs[config.provider_id] = new_config
            logger.debug(f"Saved config for provider: {config.provider_id}")

    async def get(self, provider_id: str) -> Optional[ProviderConfigSnapshot]:
        """Retrieve provider configuration by ID."""
        with self._lock:
            return self._configs.get(provider_id)

    async def get_all(self) -> List[ProviderConfigSnapshot]:
        """Retrieve all provider configurations."""
        with self._lock:
            return list(self._configs.values())

    async def delete(self, provider_id: str) -> bool:
        """Delete provider configuration."""
        with self._lock:
            if provider_id in self._configs:
                del self._configs[provider_id]
                self._versions.pop(provider_id, None)
                logger.debug(f"Deleted config for provider: {provider_id}")
                return True
            return False

    async def exists(self, provider_id: str) -> bool:
        """Check if provider configuration exists."""
        with self._lock:
            return provider_id in self._configs

    def clear(self) -> None:
        """Clear all configurations (for testing)."""
        with self._lock:
            self._configs.clear()
            self._versions.clear()


class SQLiteProviderConfigRepository:
    """SQLite implementation of provider config repository.

    Provides durable storage with optimistic concurrency control.
    """

    def __init__(self, database: Database):
        """Initialize with database connection.

        Args:
            database: Database instance for connections
        """
        self._db = database

    async def save(self, config: ProviderConfigSnapshot) -> None:
        """Save provider configuration with optimistic locking.

        Args:
            config: Provider configuration to save

        Raises:
            ConcurrentModificationError: If version conflict detected
            PersistenceError: If save operation fails
        """
        try:
            async with self._db.transaction() as conn:
                # Check existing version
                cursor = await conn.execute(
                    "SELECT version FROM provider_configs WHERE provider_id = ?",
                    (config.provider_id,),
                )
                row = await cursor.fetchone()

                config_json = json.dumps(config.to_dict())
                now = datetime.now(timezone.utc).isoformat()

                if row is None:
                    # Insert new config
                    await conn.execute(
                        """
                        INSERT INTO provider_configs
                        (provider_id, mode, config_json, enabled, version, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            config.provider_id,
                            config.mode,
                            config_json,
                            1 if config.enabled else 0,
                            now,
                            now,
                        ),
                    )
                    logger.debug(f"Inserted new config for provider: {config.provider_id}")
                else:
                    # Update existing config with version increment
                    current_version = row[0]
                    new_version = current_version + 1

                    result = await conn.execute(
                        """
                        UPDATE provider_configs
                        SET mode = ?, config_json = ?, enabled = ?,
                            version = ?, updated_at = ?
                        WHERE provider_id = ? AND version = ?
                        """,
                        (
                            config.mode,
                            config_json,
                            1 if config.enabled else 0,
                            new_version,
                            now,
                            config.provider_id,
                            current_version,
                        ),
                    )

                    if result.rowcount == 0:
                        raise ConcurrentModificationError(
                            config.provider_id,
                            current_version,
                            current_version + 1,
                        )

                    logger.debug(
                        f"Updated config for provider: {config.provider_id} "
                        f"(version {current_version} -> {new_version})"
                    )

        except ConcurrentModificationError:
            raise
        except Exception as e:
            logger.error(f"Failed to save provider config: {e}")
            raise PersistenceError(f"Failed to save provider config: {e}") from e

    async def get(self, provider_id: str) -> Optional[ProviderConfigSnapshot]:
        """Retrieve provider configuration by ID.

        Args:
            provider_id: Provider identifier

        Returns:
            Configuration snapshot if found, None otherwise
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT config_json FROM provider_configs WHERE provider_id = ?",
                    (provider_id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    return None

                config_data = json.loads(row[0])
                return ProviderConfigSnapshot.from_dict(config_data)

        except Exception as e:
            logger.error(f"Failed to get provider config: {e}")
            raise PersistenceError(f"Failed to get provider config: {e}") from e

    async def get_all(self) -> List[ProviderConfigSnapshot]:
        """Retrieve all provider configurations.

        Returns:
            List of all stored configurations
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute("SELECT config_json FROM provider_configs WHERE enabled = 1")
                rows = await cursor.fetchall()

                configs = []
                for row in rows:
                    try:
                        config_data = json.loads(row[0])
                        configs.append(ProviderConfigSnapshot.from_dict(config_data))
                    except Exception as e:
                        logger.warning(f"Failed to deserialize config: {e}")
                        continue

                return configs

        except Exception as e:
            logger.error(f"Failed to get all provider configs: {e}")
            raise PersistenceError(f"Failed to get all provider configs: {e}") from e

    async def delete(self, provider_id: str) -> bool:
        """Delete provider configuration (soft delete by disabling).

        Args:
            provider_id: Provider identifier

        Returns:
            True if deleted, False if not found
        """
        try:
            async with self._db.transaction() as conn:
                # Soft delete - mark as disabled
                result = await conn.execute(
                    """
                    UPDATE provider_configs
                    SET enabled = 0, updated_at = ?
                    WHERE provider_id = ? AND enabled = 1
                    """,
                    (datetime.now(timezone.utc).isoformat(), provider_id),
                )

                deleted = result.rowcount > 0
                if deleted:
                    logger.debug(f"Soft-deleted config for provider: {provider_id}")

                return deleted

        except Exception as e:
            logger.error(f"Failed to delete provider config: {e}")
            raise PersistenceError(f"Failed to delete provider config: {e}") from e

    async def hard_delete(self, provider_id: str) -> bool:
        """Permanently delete provider configuration.

        Use with caution - this removes all history.

        Args:
            provider_id: Provider identifier

        Returns:
            True if deleted, False if not found
        """
        try:
            async with self._db.transaction() as conn:
                result = await conn.execute(
                    "DELETE FROM provider_configs WHERE provider_id = ?",
                    (provider_id,),
                )

                deleted = result.rowcount > 0
                if deleted:
                    logger.info(f"Hard-deleted config for provider: {provider_id}")

                return deleted

        except Exception as e:
            logger.error(f"Failed to hard-delete provider config: {e}")
            raise PersistenceError(f"Failed to hard-delete provider config: {e}") from e

    async def exists(self, provider_id: str) -> bool:
        """Check if provider configuration exists.

        Args:
            provider_id: Provider identifier

        Returns:
            True if exists and enabled, False otherwise
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT 1 FROM provider_configs WHERE provider_id = ? AND enabled = 1",
                    (provider_id,),
                )
                row = await cursor.fetchone()
                return row is not None

        except Exception as e:
            logger.error(f"Failed to check provider existence: {e}")
            raise PersistenceError(f"Failed to check provider existence: {e}") from e

    async def get_with_version(self, provider_id: str) -> Optional[tuple[ProviderConfigSnapshot, int]]:
        """Get configuration with its version for optimistic locking.

        Args:
            provider_id: Provider identifier

        Returns:
            Tuple of (config, version) if found, None otherwise
        """
        try:
            async with self._db.connection() as conn:
                cursor = await conn.execute(
                    "SELECT config_json, version FROM provider_configs WHERE provider_id = ?",
                    (provider_id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    return None

                config_data = json.loads(row[0])
                return (ProviderConfigSnapshot.from_dict(config_data), row[1])

        except Exception as e:
            logger.error(f"Failed to get provider config with version: {e}")
            raise PersistenceError(f"Failed to get provider config with version: {e}") from e

    async def update_last_started(self, provider_id: str) -> None:
        """Update the last_started_at timestamp.

        Args:
            provider_id: Provider identifier
        """
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    """
                    UPDATE provider_configs
                    SET last_started_at = ?, updated_at = ?
                    WHERE provider_id = ?
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                        provider_id,
                    ),
                )

        except Exception as e:
            logger.error(f"Failed to update last_started_at: {e}")
            # Non-critical operation, don't raise

    async def update_failure_count(self, provider_id: str, consecutive_failures: int) -> None:
        """Update the consecutive failure count.

        Args:
            provider_id: Provider identifier
            consecutive_failures: Current failure count
        """
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    """
                    UPDATE provider_configs
                    SET consecutive_failures = ?, updated_at = ?
                    WHERE provider_id = ?
                    """,
                    (
                        consecutive_failures,
                        datetime.now(timezone.utc).isoformat(),
                        provider_id,
                    ),
                )

        except Exception as e:
            logger.error(f"Failed to update failure count: {e}")
            # Non-critical operation, don't raise
