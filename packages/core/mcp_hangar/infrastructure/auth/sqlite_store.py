"""SQLite-based persistent storage for API keys and roles.

Provides lightweight persistent storage for single-instance deployments.
Uses WAL mode for better concurrent read performance.

For multi-instance deployments, use PostgresApiKeyStore instead.

Note: This store emits domain events for all write operations via
an optional event_publisher callback. For full CQRS integration,
inject the EventBus.publish method as the event_publisher.
"""

import json
import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import structlog

from ...domain.contracts.authentication import ApiKeyMetadata, IApiKeyStore
from ...domain.contracts.authorization import IRoleStore
from ...domain.events import ApiKeyCreated, ApiKeyRevoked, RoleAssigned, RoleRevoked
from ...domain.exceptions import ExpiredCredentialsError, RevokedCredentialsError
from ...domain.security.roles import BUILTIN_ROLES
from ...domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role

logger = structlog.get_logger(__name__)


# SQLite Schema
SQLITE_SCHEMA = """
-- API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    key_id TEXT NOT NULL UNIQUE,
    principal_id TEXT NOT NULL,
    name TEXT NOT NULL,
    tenant_id TEXT,
    groups TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_api_keys_principal_id ON api_keys(principal_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);

-- Roles table
CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    description TEXT,
    permissions TEXT NOT NULL DEFAULT '[]',
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Role assignments table
CREATE TABLE IF NOT EXISTS role_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id TEXT NOT NULL,
    role_name TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE,
    scope TEXT NOT NULL DEFAULT 'global',
    assigned_at TEXT NOT NULL,
    assigned_by TEXT,
    UNIQUE(principal_id, role_name, scope)
);

CREATE INDEX IF NOT EXISTS idx_role_assignments_principal_scope
    ON role_assignments(principal_id, scope);
"""


class SQLiteApiKeyStore(IApiKeyStore):
    """SQLite-based API key store.

    Suitable for single-instance deployments or development.
    Uses WAL mode for better concurrent read performance.

    WARNING: For multi-instance deployments, use PostgresApiKeyStore
    which provides proper distributed locking.

    Events emitted:
    - ApiKeyCreated: When a new key is created
    - ApiKeyRevoked: When a key is revoked
    """

    MAX_KEYS_PER_PRINCIPAL = 100

    def __init__(
        self,
        db_path: str | Path,
        event_publisher: Callable | None = None,
    ):
        """Initialize the SQLite store.

        Args:
            db_path: Path to SQLite database file.
            event_publisher: Optional callback for publishing domain events.
                For CQRS integration, pass EventBus.publish.
        """
        self._db_path = str(db_path)
        self._local = threading.local()
        self._initialized = False
        self._event_publisher = event_publisher

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
        return self._local.connection

    def initialize(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return

        conn = self._get_connection()
        conn.executescript(SQLITE_SCHEMA)
        conn.commit()
        self._initialized = True
        logger.info("sqlite_api_key_store_initialized", db_path=self._db_path)

    def get_principal_for_key(self, key_hash: str) -> Principal | None:
        """Look up principal for an API key hash."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT principal_id, tenant_id, groups, name, key_id,
                   expires_at, revoked, metadata
            FROM api_keys
            WHERE key_hash = ?
        """,
            (key_hash,),
        )

        row = cursor.fetchone()
        if row is None:
            return None

        # Check revocation
        if row["revoked"]:
            raise RevokedCredentialsError(
                message="API key has been revoked",
                auth_method="api_key",
            )

        # Check expiration
        if row["expires_at"]:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at < datetime.now(UTC):
                raise ExpiredCredentialsError(
                    message="API key has expired",
                    auth_method="api_key",
                    expired_at=expires_at.timestamp(),
                )

        # Update last_used_at
        try:
            conn.execute(
                """
                UPDATE api_keys
                SET last_used_at = ?
                WHERE key_hash = ?
            """,
                (datetime.now(UTC).isoformat(), key_hash),
            )
            conn.commit()
        except Exception as e:
            logger.warning("failed_to_update_last_used", error=str(e))

        # Parse groups from JSON
        groups = json.loads(row["groups"]) if row["groups"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}

        return Principal(
            id=PrincipalId(row["principal_id"]),
            type=PrincipalType.SERVICE_ACCOUNT,
            tenant_id=row["tenant_id"],
            groups=frozenset(groups),
            metadata={"key_id": row["key_id"], "key_name": row["name"], **metadata},
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

        conn = self._get_connection()

        # Check key count for principal
        cursor = conn.execute(
            """
            SELECT COUNT(*) as count FROM api_keys
            WHERE principal_id = ? AND revoked = 0
        """,
            (principal_id,),
        )
        count = cursor.fetchone()["count"]

        if count >= self.MAX_KEYS_PER_PRINCIPAL:
            raise ValueError(f"Principal {principal_id} has reached maximum API keys ({self.MAX_KEYS_PER_PRINCIPAL})")

        # Generate key
        raw_key = ApiKeyAuthenticator.generate_key()
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        key_id = secrets.token_urlsafe(8)

        now = datetime.now(UTC).isoformat()

        # Insert
        conn.execute(
            """
            INSERT INTO api_keys
            (key_hash, key_id, principal_id, name, tenant_id, groups, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                key_hash,
                key_id,
                principal_id,
                name,
                tenant_id,
                json.dumps(list(groups or [])),
                now,
                expires_at.isoformat() if expires_at else None,
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
        conn = self._get_connection()

        # Get principal_id before revoking (for event)
        cursor = conn.execute(
            """
            SELECT principal_id FROM api_keys WHERE key_id = ? AND revoked = 0
        """,
            (key_id,),
        )
        row = cursor.fetchone()
        principal_id = row["principal_id"] if row else None

        cursor = conn.execute(
            """
            UPDATE api_keys
            SET revoked = 1, revoked_at = ?
            WHERE key_id = ? AND revoked = 0
        """,
            (datetime.now(UTC).isoformat(), key_id),
        )
        conn.commit()

        if cursor.rowcount > 0:
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
        conn = self._get_connection()

        cursor = conn.execute(
            """
            SELECT key_id, name, principal_id, created_at,
                   expires_at, last_used_at, revoked
            FROM api_keys
            WHERE principal_id = ?
            ORDER BY created_at DESC
        """,
            (principal_id,),
        )

        return [
            ApiKeyMetadata(
                key_id=row["key_id"],
                name=row["name"],
                principal_id=row["principal_id"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                last_used_at=datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None,
                revoked=bool(row["revoked"]),
            )
            for row in cursor.fetchall()
        ]

    def count_keys(self, principal_id: str) -> int:
        """Count active keys for a principal."""
        conn = self._get_connection()

        cursor = conn.execute(
            """
            SELECT COUNT(*) as count FROM api_keys
            WHERE principal_id = ? AND revoked = 0
        """,
            (principal_id,),
        )

        return cursor.fetchone()["count"]

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.commit()  # Ensure any pending changes
            except Exception:
                pass
            self._local.connection.close()
            self._local.connection = None
        self._initialized = False


class SQLiteRoleStore(IRoleStore):
    """SQLite-based role store.

    Suitable for single-instance deployments or development.

    Events emitted:
    - RoleAssigned: When a role is assigned to a principal
    - RoleRevoked: When a role is revoked from a principal
    """

    def __init__(
        self,
        db_path: str | Path,
        event_publisher: Callable | None = None,
    ):
        """Initialize the SQLite store.

        Args:
            db_path: Path to SQLite database file.
            event_publisher: Optional callback for publishing domain events.
        """
        self._db_path = str(db_path)
        self._local = threading.local()
        self._initialized = False
        self._event_publisher = event_publisher

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
        return self._local.connection

    def initialize(self) -> None:
        """Create tables and seed built-in roles."""
        if self._initialized:
            return

        conn = self._get_connection()
        conn.executescript(SQLITE_SCHEMA)

        # Seed built-in roles
        now = datetime.now(UTC).isoformat()
        for role_name, role in BUILTIN_ROLES.items():
            permissions_json = json.dumps(
                [
                    {"resource_type": p.resource_type, "action": p.action, "resource_id": p.resource_id}
                    for p in role.permissions
                ]
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO roles (name, description, permissions, is_builtin, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
            """,
                (role_name, role.description, permissions_json, now, now),
            )

        conn.commit()
        self._initialized = True
        logger.info("sqlite_role_store_initialized", db_path=self._db_path)

    def get_role(self, role_name: str) -> Role | None:
        """Get role by name."""
        conn = self._get_connection()

        cursor = conn.execute(
            """
            SELECT name, description, permissions
            FROM roles
            WHERE name = ?
        """,
            (role_name,),
        )

        row = cursor.fetchone()
        if row is None:
            return None

        permissions_list = json.loads(row["permissions"]) if row["permissions"] else []
        permissions = frozenset(
            Permission(
                resource_type=p["resource_type"],
                action=p["action"],
                resource_id=p.get("resource_id", "*"),
            )
            for p in permissions_list
        )

        return Role(name=row["name"], description=row["description"] or "", permissions=permissions)

    def add_role(self, role: Role) -> None:
        """Add a custom role."""
        conn = self._get_connection()

        permissions_json = json.dumps(
            [
                {"resource_type": p.resource_type, "action": p.action, "resource_id": p.resource_id}
                for p in role.permissions
            ]
        )

        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO roles (name, description, permissions, is_builtin, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
        """,
            (role.name, role.description, permissions_json, now, now),
        )

        conn.commit()
        logger.info("role_created", role_name=role.name)

    def get_roles_for_principal(
        self,
        principal_id: str,
        scope: str = "*",
    ) -> list[Role]:
        """Get all roles assigned to a principal."""
        conn = self._get_connection()

        if scope == "*":
            cursor = conn.execute(
                """
                SELECT r.name, r.description, r.permissions
                FROM roles r
                JOIN role_assignments a ON r.name = a.role_name
                WHERE a.principal_id = ?
            """,
                (principal_id,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT r.name, r.description, r.permissions
                FROM roles r
                JOIN role_assignments a ON r.name = a.role_name
                WHERE a.principal_id = ? AND (a.scope = ? OR a.scope = 'global')
            """,
                (principal_id, scope),
            )

        roles = []
        for row in cursor.fetchall():
            permissions_list = json.loads(row["permissions"]) if row["permissions"] else []
            permissions = frozenset(
                Permission(
                    resource_type=p["resource_type"],
                    action=p["action"],
                    resource_id=p.get("resource_id", "*"),
                )
                for p in permissions_list
            )
            roles.append(Role(name=row["name"], description=row["description"] or "", permissions=permissions))

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
        conn = self._get_connection()

        # Verify role exists
        cursor = conn.execute("SELECT 1 FROM roles WHERE name = ?", (role_name,))
        if cursor.fetchone() is None:
            raise ValueError(f"Unknown role: {role_name}")

        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO role_assignments (principal_id, role_name, scope, assigned_at)
            VALUES (?, ?, ?, ?)
        """,
            (principal_id, role_name, scope, now),
        )

        conn.commit()

        # Only emit event if actually inserted (not ignored due to duplicate)
        if cursor.rowcount > 0:
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
        conn = self._get_connection()

        cursor = conn.execute(
            """
            DELETE FROM role_assignments
            WHERE principal_id = ? AND role_name = ? AND scope = ?
        """,
            (principal_id, role_name, scope),
        )

        conn.commit()

        if cursor.rowcount > 0:
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

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                self._local.connection.commit()  # Ensure any pending changes
                # Checkpoint WAL to make data visible to new connections
                self._local.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._local.connection.close()
            self._local.connection = None
        self._initialized = False
