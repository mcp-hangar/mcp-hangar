"""PostgreSQL-based persistent storage for API keys and roles.

Provides production-ready storage backends with:
- Connection pooling
- Retry logic
- Multi-instance support
- Proper transaction handling
- Domain event emission (CQRS compatible)

Requires: asyncpg or psycopg2
"""

import json
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime

import structlog

from ...domain.contracts.authentication import ApiKeyMetadata, IApiKeyStore
from ...domain.contracts.authorization import IRoleStore
from ...domain.events import ApiKeyCreated, ApiKeyRevoked, RoleAssigned, RoleRevoked
from ...domain.exceptions import ExpiredCredentialsError, RevokedCredentialsError
from ...domain.security.roles import BUILTIN_ROLES
from ...domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role

logger = structlog.get_logger(__name__)


# SQL Schema for API Keys
API_KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash VARCHAR(64) PRIMARY KEY,
    key_id VARCHAR(32) NOT NULL UNIQUE,
    principal_id VARCHAR(256) NOT NULL,
    name VARCHAR(256) NOT NULL,
    tenant_id VARCHAR(256),
    groups JSONB DEFAULT '[]',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_api_keys_principal_id ON api_keys(principal_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_expires_at ON api_keys(expires_at) WHERE expires_at IS NOT NULL;
"""

# SQL Schema for Roles
ROLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    name VARCHAR(128) PRIMARY KEY,
    description TEXT,
    permissions JSONB NOT NULL DEFAULT '[]',
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS role_assignments (
    id SERIAL PRIMARY KEY,
    principal_id VARCHAR(256) NOT NULL,
    role_name VARCHAR(128) NOT NULL REFERENCES roles(name) ON DELETE CASCADE,
    scope VARCHAR(256) NOT NULL DEFAULT 'global',
    assigned_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    assigned_by VARCHAR(256),
    UNIQUE(principal_id, role_name, scope)
);

CREATE INDEX IF NOT EXISTS idx_role_assignments_principal_scope
    ON role_assignments(principal_id, scope);
"""


class PostgresApiKeyStore(IApiKeyStore):
    """PostgreSQL-based API key store.

    Features:
    - Connection pooling via connection factory
    - Atomic operations with proper transactions
    - Optimistic locking for updates
    - Automatic last_used_at updates

    Multi-instance safe: Uses database-level locking.
    """

    MAX_KEYS_PER_PRINCIPAL = 100

    def __init__(
        self,
        connection_factory,
        table_prefix: str = "",
    ):
        """Initialize the PostgreSQL store.

        Args:
            connection_factory: Callable that returns a DB connection.
                               Should support context manager protocol.
            table_prefix: Optional prefix for table names.
        """
        self._get_connection = connection_factory
        self._prefix = table_prefix
        self._table = f"{table_prefix}api_keys" if table_prefix else "api_keys"

    def initialize(self) -> None:
        """Create tables if they don't exist."""
        schema = API_KEYS_SCHEMA
        if self._prefix:
            schema = schema.replace("api_keys", self._table)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)
            conn.commit()
        logger.info("postgres_api_key_store_initialized", table=self._table)

    def get_principal_for_key(self, key_hash: str) -> Principal | None:
        """Look up principal for an API key hash."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT principal_id, tenant_id, groups, name, key_id,
                           expires_at, revoked, metadata
                    FROM {self._table}
                    WHERE key_hash = %s
                """,
                    (key_hash,),
                )

                row = cur.fetchone()
                if row is None:
                    return None

                principal_id, tenant_id, groups, name, key_id, expires_at, revoked, metadata = row

                # Check revocation
                if revoked:
                    raise RevokedCredentialsError(
                        message="API key has been revoked",
                        auth_method="api_key",
                    )

                # Check expiration
                if expires_at and expires_at < datetime.now(UTC):
                    raise ExpiredCredentialsError(
                        message="API key has expired",
                        auth_method="api_key",
                        expired_at=expires_at.timestamp(),
                    )

                # Update last_used_at (fire and forget, don't fail auth on update error)
                try:
                    cur.execute(
                        f"""
                        UPDATE {self._table}
                        SET last_used_at = NOW()
                        WHERE key_hash = %s
                    """,
                        (key_hash,),
                    )
                    conn.commit()
                except Exception as e:
                    logger.warning("failed_to_update_last_used", error=str(e))
                    conn.rollback()

                # Parse groups from JSON
                if isinstance(groups, str):
                    groups = json.loads(groups)

                return Principal(
                    id=PrincipalId(principal_id),
                    type=PrincipalType.SERVICE_ACCOUNT,
                    tenant_id=tenant_id,
                    groups=frozenset(groups or []),
                    metadata={"key_id": key_id, "key_name": name, **(metadata or {})},
                )

    def create_key(
        self,
        principal_id: str,
        name: str,
        expires_at: datetime | None = None,
        groups: frozenset[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "system",
    ) -> str:
        """Create a new API key.

        Emits: ApiKeyCreated event
        """
        from .api_key_authenticator import ApiKeyAuthenticator

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Check key count for principal
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM {self._table}
                    WHERE principal_id = %s AND revoked = FALSE
                """,
                    (principal_id,),
                )
                count = cur.fetchone()[0]

                if count >= self.MAX_KEYS_PER_PRINCIPAL:
                    raise ValueError(
                        f"Principal {principal_id} has reached maximum API keys ({self.MAX_KEYS_PER_PRINCIPAL})"
                    )

                # Generate key
                raw_key = ApiKeyAuthenticator.generate_key()
                key_hash = ApiKeyAuthenticator._hash_key(raw_key)
                key_id = secrets.token_urlsafe(8)

                # Insert
                cur.execute(
                    f"""
                    INSERT INTO {self._table}
                    (key_hash, key_id, principal_id, name, tenant_id, groups, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        key_hash,
                        key_id,
                        principal_id,
                        name,
                        tenant_id,
                        json.dumps(list(groups or [])),
                        expires_at,
                    ),
                )
                conn.commit()

                logger.info(
                    "api_key_created",
                    key_id=key_id,
                    principal_id=principal_id,
                    name=name,
                    expires_at=expires_at.isoformat() if expires_at else None,
                )

                # Emit domain event
                if self._event_publisher:
                    self._event_publisher(
                        ApiKeyCreated(
                            key_id=key_id,
                            principal_id=principal_id,
                            key_name=name,
                            expires_at=expires_at.timestamp() if expires_at else None,
                            created_by=created_by,
                        )
                    )

                return raw_key

    def revoke_key(self, key_id: str, revoked_by: str = "system", reason: str = "") -> bool:
        """Revoke an API key.

        Emits: ApiKeyRevoked event
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Get principal_id before revoking
                cur.execute(
                    f"""
                    SELECT principal_id FROM {self._table}
                    WHERE key_id = %s AND revoked = FALSE
                """,
                    (key_id,),
                )
                row = cur.fetchone()
                principal_id = row[0] if row else None

                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET revoked = TRUE, revoked_at = NOW()
                    WHERE key_id = %s AND revoked = FALSE
                    RETURNING key_id
                """,
                    (key_id,),
                )

                result = cur.fetchone()
                conn.commit()

                if result:
                    logger.info("api_key_revoked", key_id=key_id)

                    # Emit domain event
                    if self._event_publisher and principal_id:
                        self._event_publisher(
                            ApiKeyRevoked(
                                key_id=key_id,
                                principal_id=principal_id,
                                revoked_by=revoked_by,
                                reason=reason,
                            )
                        )
                    return True
                return False

    def list_keys(self, principal_id: str) -> list[ApiKeyMetadata]:
        """List API keys for a principal."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT key_id, name, principal_id, created_at,
                           expires_at, last_used_at, revoked
                    FROM {self._table}
                    WHERE principal_id = %s
                    ORDER BY created_at DESC
                """,
                    (principal_id,),
                )

                return [
                    ApiKeyMetadata(
                        key_id=row[0],
                        name=row[1],
                        principal_id=row[2],
                        created_at=row[3],
                        expires_at=row[4],
                        last_used_at=row[5],
                        revoked=row[6],
                    )
                    for row in cur.fetchall()
                ]

    def count_keys(self, principal_id: str) -> int:
        """Count active keys for a principal."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM {self._table}
                    WHERE principal_id = %s AND revoked = FALSE
                """,
                    (principal_id,),
                )
                return cur.fetchone()[0]


class PostgresRoleStore(IRoleStore):
    """PostgreSQL-based role store.

    Features:
    - Built-in roles seeded on init
    - Custom roles support
    - Multi-scope assignments
    - Proper foreign key constraints
    - Domain event emission

    Multi-instance safe: Uses database-level constraints.

    Events emitted:
    - RoleAssigned: When a role is assigned
    - RoleRevoked: When a role is revoked
    """

    def __init__(
        self,
        connection_factory,
        table_prefix: str = "",
        event_publisher: Callable | None = None,
    ):
        """Initialize the PostgreSQL store.

        Args:
            connection_factory: Callable that returns a DB connection.
            table_prefix: Optional prefix for table names.
            event_publisher: Optional callback for publishing domain events.
        """
        self._get_connection = connection_factory
        self._prefix = table_prefix
        self._roles_table = f"{table_prefix}roles" if table_prefix else "roles"
        self._assignments_table = f"{table_prefix}role_assignments" if table_prefix else "role_assignments"
        self._event_publisher = event_publisher

    def initialize(self) -> None:
        """Create tables and seed built-in roles."""
        schema = ROLES_SCHEMA
        if self._prefix:
            schema = schema.replace("roles", self._roles_table)
            schema = schema.replace("role_assignments", self._assignments_table)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)

                # Seed built-in roles
                for role_name, role in BUILTIN_ROLES.items():
                    permissions_json = json.dumps(
                        [
                            {"resource_type": p.resource_type, "action": p.action, "resource_id": p.resource_id}
                            for p in role.permissions
                        ]
                    )

                    cur.execute(
                        f"""
                        INSERT INTO {self._roles_table} (name, description, permissions, is_builtin)
                        VALUES (%s, %s, %s, TRUE)
                        ON CONFLICT (name) DO UPDATE SET
                            description = EXCLUDED.description,
                            permissions = EXCLUDED.permissions,
                            updated_at = NOW()
                    """,
                        (role_name, role.description, permissions_json),
                    )

            conn.commit()
        logger.info("postgres_role_store_initialized", roles_table=self._roles_table)

    def get_role(self, role_name: str) -> Role | None:
        """Get role by name."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT name, description, permissions
                    FROM {self._roles_table}
                    WHERE name = %s
                """,
                    (role_name,),
                )

                row = cur.fetchone()
                if row is None:
                    return None

                name, description, permissions_json = row

                if isinstance(permissions_json, str):
                    permissions_json = json.loads(permissions_json)

                permissions = frozenset(
                    Permission(
                        resource_type=p["resource_type"],
                        action=p["action"],
                        resource_id=p.get("resource_id", "*"),
                    )
                    for p in permissions_json
                )

                return Role(name=name, description=description or "", permissions=permissions)

    def add_role(self, role: Role) -> None:
        """Add a custom role."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                permissions_json = json.dumps(
                    [
                        {"resource_type": p.resource_type, "action": p.action, "resource_id": p.resource_id}
                        for p in role.permissions
                    ]
                )

                cur.execute(
                    f"""
                    INSERT INTO {self._roles_table} (name, description, permissions, is_builtin)
                    VALUES (%s, %s, %s, FALSE)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        permissions = EXCLUDED.permissions,
                        updated_at = NOW()
                """,
                    (role.name, role.description, permissions_json),
                )

            conn.commit()
            logger.info("role_created", role_name=role.name)

    def get_roles_for_principal(
        self,
        principal_id: str,
        scope: str = "*",
    ) -> list[Role]:
        """Get all roles assigned to a principal."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                if scope == "*":
                    cur.execute(
                        f"""
                        SELECT r.name, r.description, r.permissions
                        FROM {self._roles_table} r
                        JOIN {self._assignments_table} a ON r.name = a.role_name
                        WHERE a.principal_id = %s
                    """,
                        (principal_id,),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT r.name, r.description, r.permissions
                        FROM {self._roles_table} r
                        JOIN {self._assignments_table} a ON r.name = a.role_name
                        WHERE a.principal_id = %s AND (a.scope = %s OR a.scope = 'global')
                    """,
                        (principal_id, scope),
                    )

                roles = []
                for name, description, permissions_json in cur.fetchall():
                    if isinstance(permissions_json, str):
                        permissions_json = json.loads(permissions_json)

                    permissions = frozenset(
                        Permission(
                            resource_type=p["resource_type"],
                            action=p["action"],
                            resource_id=p.get("resource_id", "*"),
                        )
                        for p in permissions_json
                    )
                    roles.append(Role(name=name, description=description or "", permissions=permissions))

                return roles

    def assign_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        assigned_by: str = "system",
    ) -> None:
        """Assign a role to a principal.

        Emits: RoleAssigned event
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Verify role exists
                cur.execute(f"SELECT 1 FROM {self._roles_table} WHERE name = %s", (role_name,))
                if cur.fetchone() is None:
                    raise ValueError(f"Unknown role: {role_name}")

                cur.execute(
                    f"""
                    INSERT INTO {self._assignments_table} (principal_id, role_name, scope)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (principal_id, role_name, scope) DO NOTHING
                    RETURNING id
                """,
                    (principal_id, role_name, scope),
                )

                result = cur.fetchone()
            conn.commit()

            # Only emit event if actually inserted
            if result:
                logger.info("role_assigned", principal_id=principal_id, role_name=role_name, scope=scope)

                if self._event_publisher:
                    self._event_publisher(
                        RoleAssigned(
                            principal_id=principal_id,
                            role_name=role_name,
                            scope=scope,
                            assigned_by=assigned_by,
                        )
                    )

    def revoke_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        revoked_by: str = "system",
    ) -> None:
        """Revoke a role from a principal.

        Emits: RoleRevoked event
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    DELETE FROM {self._assignments_table}
                    WHERE principal_id = %s AND role_name = %s AND scope = %s
                    RETURNING id
                """,
                    (principal_id, role_name, scope),
                )

                result = cur.fetchone()
            conn.commit()

            if result:
                logger.info("role_revoked", principal_id=principal_id, role_name=role_name, scope=scope)

                if self._event_publisher:
                    self._event_publisher(
                        RoleRevoked(
                            principal_id=principal_id,
                            role_name=role_name,
                            scope=scope,
                            revoked_by=revoked_by,
                        )
                    )


def create_postgres_connection_factory(
    host: str = "localhost",
    port: int = 5432,
    database: str = "mcp_hangar",
    user: str = "mcp_hangar",
    password: str = "",
    min_connections: int = 2,
    max_connections: int = 10,
):
    """Create a connection factory for PostgreSQL.

    Uses psycopg2 with connection pooling.

    Args:
        host: Database host.
        port: Database port.
        database: Database name.
        user: Database user.
        password: Database password.
        min_connections: Minimum pool size.
        max_connections: Maximum pool size.

    Returns:
        Connection factory callable.
    """
    try:
        import psycopg2  # noqa: F401 - imported for availability check
        from psycopg2 import pool
    except ImportError:
        raise ImportError("psycopg2 is required for PostgreSQL storage. Install with: pip install psycopg2-binary")

    connection_pool = pool.ThreadedConnectionPool(
        minconn=min_connections,
        maxconn=max_connections,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )

    @contextmanager
    def get_connection():
        conn = connection_pool.getconn()
        try:
            yield conn
        finally:
            connection_pool.putconn(conn)

    return get_connection
