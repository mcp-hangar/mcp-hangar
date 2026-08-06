"""PostgreSQL adapter for `IMcpServerConfigRepository`.

Mirrors `infrastructure.persistence.config_repository.SQLiteMcpServerConfigRepository`
so that mcp_server configuration -- the record a recovering process reads to
decide what to restart -- behaves identically whether the deployment is a
single SQLite file or a PostgreSQL cluster. The optimistic-locking contract in
particular has to survive the port unchanged: two operators racing to update
the same mcp_server must get the same `ConcurrentModificationError`, on the
same conditions, on either backend.
"""

from datetime import UTC, datetime
import json
from typing import Any

from mcp_hangar.domain.contracts.persistence import (
    ConcurrentModificationError,
    McpServerConfigSnapshot,
    PersistenceError,
)
from mcp_hangar.infrastructure.persistence.database_common import IConnectionFactory
from mcp_hangar.logging_config import get_logger

from .management_lease import FLEET_MANAGEMENT

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_server_configs (
    mcp_server_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    config_json JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_started_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mcp_server_configs_enabled
    ON mcp_server_configs(enabled);
"""


def _config_data(raw: Any) -> dict[str, Any]:
    """Normalize a `config_json` cell to a dict.

    psycopg2 decodes JSONB columns to a Python object automatically in the
    common case, but some cursor/type configurations hand back the raw JSON
    text instead -- tolerate both rather than assuming one.
    """
    return raw if isinstance(raw, dict) else json.loads(raw)


class PostgresMcpServerConfigRepository:
    """PostgreSQL-backed store for mcp_server configuration.

    Durable storage with optimistic concurrency control, for deployments that
    run more than one gateway process against a shared database. Delete is
    soft (an `enabled` flag flip) so the config an mcp_server started under
    stays inspectable after it is turned off; `hard_delete` exists separately
    for callers that actually want the row gone.

    Every method that touches a connection rolls back explicitly on failure.
    `IConnectionFactory.get_connection()` returns a pooled connection to the
    pool once its `with` block exits, regardless of transaction state -- a
    query or commit that raises without an explicit rollback leaves that
    connection in an aborted transaction, which then poisons the *next*
    caller to borrow it (any concurrent reader or writer), not just the one
    that failed.
    """

    def __init__(self, connection_factory: IConnectionFactory) -> None:
        """Initialize and create the table if it is missing.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This repository
                knows SQL; it deliberately does not know psycopg2, pooling, or
                how a connection is obtained.
        """
        self._connections = connection_factory
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(_SCHEMA)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    async def save(self, config: McpServerConfigSnapshot) -> None:
        """Save mcp_server configuration with optimistic locking.

        Args:
            config: McpServer configuration to save

        Raises:
            ConcurrentModificationError: If version conflict detected
            PersistenceError: If save operation fails
        """
        try:
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT version FROM mcp_server_configs WHERE mcp_server_id = %s",
                            (config.mcp_server_id,),
                        )
                        row = cur.fetchone()

                        config_json = json.dumps(config.to_dict())
                        now = datetime.now(UTC).isoformat()

                        if row is None:
                            # Insert new config
                            cur.execute(
                                """
                                INSERT INTO mcp_server_configs
                                (mcp_server_id, mode, config_json, enabled, version, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, 1, %s, %s)
                                """,
                                (
                                    config.mcp_server_id,
                                    config.mode,
                                    config_json,
                                    config.enabled,
                                    now,
                                    now,
                                ),
                            )
                            conn.commit()
                            logger.debug(f"Inserted new config for mcp_server: {config.mcp_server_id}")
                        else:
                            # Update existing config with version increment
                            current_version = row[0]
                            new_version = current_version + 1

                            cur.execute(
                                """
                                UPDATE mcp_server_configs
                                SET mode = %s, config_json = %s, enabled = %s,
                                    version = %s, updated_at = %s
                                WHERE mcp_server_id = %s AND version = %s
                                """,
                                (
                                    config.mode,
                                    config_json,
                                    config.enabled,
                                    new_version,
                                    now,
                                    config.mcp_server_id,
                                    current_version,
                                ),
                            )

                            if cur.rowcount == 0:
                                conn.rollback()
                                raise ConcurrentModificationError(
                                    config.mcp_server_id,
                                    current_version,
                                    current_version + 1,
                                )

                            conn.commit()
                            logger.debug(
                                f"Updated config for mcp_server: {config.mcp_server_id} "
                                f"(version {current_version} -> {new_version})"
                            )

                except ConcurrentModificationError:
                    raise
                except Exception:
                    # See class docstring: a pooled connection returned
                    # without a rollback stays in an aborted transaction for
                    # whichever concurrent writer borrows it next.
                    conn.rollback()
                    raise

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
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT config_json FROM mcp_server_configs WHERE mcp_server_id = %s",
                            (mcp_server_id,),
                        )
                        row = cur.fetchone()

                        if row is None:
                            return None

                        return McpServerConfigSnapshot.from_dict(_config_data(row[0]))
                except Exception:
                    # See class docstring: even a failed read leaves the
                    # connection aborted for whoever borrows it next.
                    conn.rollback()
                    raise

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to get mcp_server config: {e}")
            raise PersistenceError(f"Failed to get mcp_server config: {e}") from e

    async def get_all(self) -> list[McpServerConfigSnapshot]:
        """Retrieve all mcp_server configurations.

        Returns:
            List of all stored configurations
        """
        try:
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT config_json FROM mcp_server_configs WHERE enabled = TRUE")
                        rows = cur.fetchall()
                except Exception:
                    conn.rollback()
                    raise

                configs = []
                for row in rows:
                    try:
                        configs.append(McpServerConfigSnapshot.from_dict(_config_data(row[0])))
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
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        # Soft delete - mark as disabled
                        cur.execute(
                            """
                            UPDATE mcp_server_configs
                            SET enabled = FALSE, updated_at = %s
                            WHERE mcp_server_id = %s AND enabled = TRUE
                            """,
                            (datetime.now(UTC).isoformat(), mcp_server_id),
                        )

                        deleted = cur.rowcount > 0
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                if deleted:
                    logger.debug(f"Soft-deleted config for mcp_server: {mcp_server_id}")

                return bool(deleted)

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to delete mcp_server config: {e}")
            raise PersistenceError(f"Failed to delete mcp_server config: {e}") from e

    async def delete_while_leased(self, mcp_server_id: str, holder: str, generation: int) -> bool:
        """Delete, but only if `holder` still holds the lease at that generation.

        The condition is in the statement, not around it. The sequence this
        closes is a leader that stalls past its tenure: it is frozen, so it
        cannot notice, and its delete goes out the instant it resumes. Only the
        database can rule on it, and only at the moment of the write.

        `expires_at > now()` is part of the condition. Holding the row is not
        enough -- a tenure that lapsed without anyone taking it is still over,
        and a peer may be acquiring it right now.

        Args:
            mcp_server_id: McpServer identifier.
            holder: The instance that decided on the deletion.
            generation: The tenure it decided under.

        Returns:
            True if the row was disabled. False when the tenure had ended or the
            row was already gone.
        """
        try:
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE mcp_server_configs
                            SET enabled = FALSE, updated_at = %s
                            WHERE mcp_server_id = %s AND enabled = TRUE
                              AND EXISTS (
                                  SELECT 1 FROM management_lease
                                   WHERE name = %s AND holder = %s AND generation = %s
                                     AND expires_at > now()
                              )
                            """,
                            (
                                datetime.now(UTC).isoformat(),
                                mcp_server_id,
                                FLEET_MANAGEMENT,
                                holder,
                                generation,
                            ),
                        )
                        deleted = cur.rowcount > 0
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                return bool(deleted)

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to delete mcp_server config under a lease: {e}")
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
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM mcp_server_configs WHERE mcp_server_id = %s",
                            (mcp_server_id,),
                        )

                        deleted = cur.rowcount > 0
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                if deleted:
                    logger.info(f"Hard-deleted config for mcp_server: {mcp_server_id}")

                return bool(deleted)

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
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM mcp_server_configs WHERE mcp_server_id = %s AND enabled = TRUE",
                            (mcp_server_id,),
                        )
                        return cur.fetchone() is not None
                except Exception:
                    conn.rollback()
                    raise

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
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT config_json, version FROM mcp_server_configs WHERE mcp_server_id = %s",
                            (mcp_server_id,),
                        )
                        row = cur.fetchone()

                        if row is None:
                            return None

                        return (McpServerConfigSnapshot.from_dict(_config_data(row[0])), row[1])
                except Exception:
                    conn.rollback()
                    raise

        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as PersistenceError
            logger.error(f"Failed to get mcp_server config with version: {e}")
            raise PersistenceError(f"Failed to get mcp_server config with version: {e}") from e

    async def update_last_started(self, mcp_server_id: str) -> None:
        """Update the last_started_at timestamp.

        Args:
            mcp_server_id: McpServer identifier
        """
        try:
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE mcp_server_configs
                            SET last_started_at = %s, updated_at = %s
                            WHERE mcp_server_id = %s
                            """,
                            (
                                datetime.now(UTC).isoformat(),
                                datetime.now(UTC).isoformat(),
                                mcp_server_id,
                            ),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

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
            with self._connections.get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE mcp_server_configs
                            SET consecutive_failures = %s, updated_at = %s
                            WHERE mcp_server_id = %s
                            """,
                            (
                                consecutive_failures,
                                datetime.now(UTC).isoformat(),
                                mcp_server_id,
                            ),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        except Exception as e:  # noqa: BLE001 -- infra-boundary: non-critical, best-effort update
            logger.error(f"Failed to update failure count: {e}")
            # Non-critical operation, don't raise
