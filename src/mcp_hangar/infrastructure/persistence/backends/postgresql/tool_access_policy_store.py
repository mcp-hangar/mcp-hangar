"""PostgreSQL-based persistent storage for tool access policies.

Tool access policies decide which tools a principal may call -- deny_list,
approval_list, and allow_list patterns keyed by (scope, target_id), where
scope is "mcp_server", "group", or "member". Without a working store on a
given backend, policies set through the API are silently unpersisted: they
work for the life of the process and vanish on restart, and nothing at
startup rebuilds the in-memory resolver cache. This store is what makes
policy management durable when PostgreSQL is the configured backend, mirroring
`SQLiteToolAccessPolicyStore` so both backends are interchangeable behind
`IToolAccessPolicyStore`.

Requires: psycopg2 (installed by the `postgres` extra). This module never
imports it directly -- all connections come from the shared
`IConnectionFactory` (see `infrastructure.persistence.database_common`).
"""

import json

import structlog

from mcp_hangar.domain.contracts.authorization import IToolAccessPolicyStore
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

logger = structlog.get_logger(__name__)

# scope/target_id is the natural key: a policy is a single row per
# (scope, target_id) pair, upserted in place -- there is no history, only
# the current effective policy, same as the SQLite reference.
TOOL_ACCESS_POLICIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    scope       VARCHAR(32)  NOT NULL,
    target_id   VARCHAR(256) NOT NULL,
    allow_list  JSONB NOT NULL DEFAULT '[]',
    deny_list   JSONB NOT NULL DEFAULT '[]',
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, target_id)
);
"""


class PostgresToolAccessPolicyStore(IToolAccessPolicyStore):
    """PostgreSQL-based store for tool access policies.

    Provides durable persistence for `ToolAccessPolicy` objects keyed by
    (scope, target_id). On startup, call `list_all_policies()` and feed the
    results into `ToolAccessResolver` to rebuild the in-memory cache -- this
    store holds the source of truth, the resolver only ever holds a copy.

    Connection lifecycle (pooling, thread-safety) is owned entirely by the
    injected `IConnectionFactory`; this class only ever knows SQL.
    """

    def __init__(self, connection_factory, table_prefix: str = "") -> None:
        """Initialize the store.

        Args:
            connection_factory: An `IConnectionFactory` -- the shared port from
                `infrastructure.persistence.database_common`. This store knows
                SQL; it deliberately does not know psycopg2, pooling, or how a
                connection is obtained. One place holds that knowledge, and it
                is the factory (#779).
            table_prefix: Optional prefix for the table name.
        """
        self._connections = connection_factory
        self._table = f"{table_prefix}tool_access_policies" if table_prefix else "tool_access_policies"

    def initialize(self) -> None:
        """Create the tool_access_policies table if it does not exist."""
        schema = TOOL_ACCESS_POLICIES_SCHEMA.format(table=self._table)
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(schema)
                conn.commit()
            except Exception:
                # `IConnectionFactory.get_connection()` returns a pooled
                # connection to the pool in `finally` regardless of
                # transaction state -- without an explicit rollback here, a
                # failed CREATE TABLE leaves the connection in an aborted
                # transaction that poisons the *next* caller to borrow it
                # (any concurrent writer), not just this one.
                conn.rollback()
                raise
        logger.info("postgres_tap_store_initialized", table=self._table)

    def set_policy(
        self,
        scope: str,
        target_id: str,
        allow_list: list[str],
        deny_list: list[str],
    ) -> None:
        """Persist a tool access policy (upsert).

        Args:
            scope: "mcp_server", "group", or "member".
            target_id: Provider/group/member identifier.
            allow_list: Allowed tool patterns.
            deny_list: Denied tool patterns.
        """
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {self._table} (scope, target_id, allow_list, deny_list, updated_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (scope, target_id) DO UPDATE SET
                            allow_list = EXCLUDED.allow_list,
                            deny_list = EXCLUDED.deny_list,
                            updated_at = NOW()
                        """,
                        (scope, target_id, json.dumps(allow_list), json.dumps(deny_list)),
                    )
                conn.commit()
            except Exception:
                # See initialize() -- a pooled connection returned without a
                # rollback stays in an aborted transaction for whichever
                # concurrent writer borrows it next.
                conn.rollback()
                raise
        logger.info("tap_policy_set", scope=scope, target_id=target_id)

    def get_policy(self, scope: str, target_id: str) -> ToolAccessPolicy | None:
        """Retrieve a stored policy.

        Args:
            scope: Scope string.
            target_id: Target identifier.

        Returns:
            ToolAccessPolicy if found, None otherwise.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT allow_list, deny_list FROM {self._table} WHERE scope = %s AND target_id = %s",
                (scope, target_id),
            )
            row = cur.fetchone()
            if row is None:
                return None

            allow_list, deny_list = row
            # psycopg2 decodes JSONB columns to Python objects by default, but
            # a raw/mocked cursor (or a differently configured connection) may
            # still hand back the JSON text -- decode defensively either way.
            if isinstance(allow_list, str):
                allow_list = json.loads(allow_list)
            if isinstance(deny_list, str):
                deny_list = json.loads(deny_list)

            return ToolAccessPolicy(
                allow_list=tuple(allow_list),
                deny_list=tuple(deny_list),
            )

    def clear_policy(self, scope: str, target_id: str) -> None:
        """Remove a stored policy.

        Args:
            scope: Scope string.
            target_id: Target identifier.
        """
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {self._table} WHERE scope = %s AND target_id = %s",
                        (scope, target_id),
                    )
                conn.commit()
            except Exception:
                # See initialize() -- a pooled connection returned without a
                # rollback stays in an aborted transaction for whichever
                # concurrent writer borrows it next.
                conn.rollback()
                raise
        logger.info("tap_policy_cleared", scope=scope, target_id=target_id)

    def list_all_policies(self) -> list[tuple[str, str, list[str], list[str]]]:
        """Return all stored policies for startup replay.

        Returns:
            List of (scope, target_id, allow_list, deny_list) tuples.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT scope, target_id, allow_list, deny_list FROM {self._table}")
            rows = cur.fetchall()

            result: list[tuple[str, str, list[str], list[str]]] = []
            for scope, target_id, allow_list, deny_list in rows:
                if isinstance(allow_list, str):
                    allow_list = json.loads(allow_list)
                if isinstance(deny_list, str):
                    deny_list = json.loads(deny_list)
                result.append((scope, target_id, allow_list, deny_list))
            return result
