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
from mcp_hangar.infrastructure.persistence.database_common import postgres_ddl

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
    approval_list JSONB NOT NULL DEFAULT '[]',
    approval_timeout_seconds INTEGER,
    approval_channel VARCHAR(64),
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, target_id)
);
"""

#: Widening for a table that already exists -- ``CREATE TABLE IF NOT EXISTS``
#: does nothing to one, so a deployment that has ever written a policy keeps the
#: pre-approval columns until this runs. The approval columns are nullable
#: rather than carrying a SQL default: the default lives on
#: :class:`ToolAccessPolicy`, and NULL reads back as "whatever it says today".
TOOL_ACCESS_POLICIES_MIGRATION = """
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS approval_list JSONB NOT NULL DEFAULT '[]';
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS approval_timeout_seconds INTEGER;
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS approval_channel VARCHAR(64);
"""


#: The columns :func:`_policy_from_row` expects, in order. One definition, so a
#: SELECT and the unpacking below it cannot drift apart.
_POLICY_COLUMNS = "allow_list, deny_list, approval_list, approval_timeout_seconds, approval_channel"


def _decode(value: object) -> list[str]:
    """psycopg2 decodes JSONB by default; a mocked cursor may hand back text."""
    if isinstance(value, str):
        return list(json.loads(value))
    return list(value) if value else []


def _policy_from_row(row) -> ToolAccessPolicy:
    """Rebuild a whole policy from the :data:`_POLICY_COLUMNS` of one row.

    NULL approval columns are a row written before the gate had somewhere to
    live; they mean "nothing was gated here", not "fail the replay".
    """
    allow_list, deny_list, approval_list, timeout, channel = row

    optional: dict[str, object] = {}
    if timeout is not None:
        optional["approval_timeout_seconds"] = timeout
    if channel is not None:
        optional["approval_channel"] = channel

    return ToolAccessPolicy(
        allow_list=tuple(_decode(allow_list)),
        deny_list=tuple(_decode(deny_list)),
        approval_list=tuple(_decode(approval_list)),
        **optional,  # type: ignore[arg-type]
    )


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
        migration = TOOL_ACCESS_POLICIES_MIGRATION.format(table=self._table)
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(postgres_ddl(schema))
                    cur.execute(postgres_ddl(migration))
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

    def set_policy(self, scope: str, target_id: str, policy: ToolAccessPolicy) -> None:
        """Persist a tool access policy (upsert).

        Args:
            scope: "mcp_server", "group", or "member".
            target_id: Provider/group/member identifier.
            policy: The policy to persist, in full -- including the approval
                gate, which the store used to have no column for (#915).
        """
        with self._connections.get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {self._table} (
                            scope, target_id, allow_list, deny_list,
                            approval_list, approval_timeout_seconds, approval_channel, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (scope, target_id) DO UPDATE SET
                            allow_list = EXCLUDED.allow_list,
                            deny_list = EXCLUDED.deny_list,
                            approval_list = EXCLUDED.approval_list,
                            approval_timeout_seconds = EXCLUDED.approval_timeout_seconds,
                            approval_channel = EXCLUDED.approval_channel,
                            updated_at = NOW()
                        """,
                        (
                            scope,
                            target_id,
                            json.dumps(list(policy.allow_list)),
                            json.dumps(list(policy.deny_list)),
                            json.dumps(list(policy.approval_list)),
                            policy.approval_timeout_seconds,
                            policy.approval_channel,
                        ),
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
                f"SELECT {_POLICY_COLUMNS} FROM {self._table} WHERE scope = %s AND target_id = %s",
                (scope, target_id),
            )
            row = cur.fetchone()
            if row is None:
                return None

            return _policy_from_row(row)

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

    def list_all_policies(self) -> list[tuple[str, str, ToolAccessPolicy]]:
        """Return all stored policies for startup replay.

        Returns:
            List of (scope, target_id, policy) tuples.
        """
        with self._connections.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT scope, target_id, {_POLICY_COLUMNS} FROM {self._table}")
            return [(scope, target_id, _policy_from_row(rest)) for scope, target_id, *rest in cur.fetchall()]
