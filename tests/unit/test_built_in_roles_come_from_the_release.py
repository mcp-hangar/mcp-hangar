"""A built-in role is enforced as the running release defines it, whatever its stored row says (#1988).

Replicas sharing an auth store each seed their own release's built-in roles at
startup, so the stored row is whichever replica started last. A 2.31.0 replica
took `mcp_servers:peer_identity` away from `kubernetes-operator` on every
2.32.0 replica. The rows stay, for older replicas that still read them; this
release does not read a built-in role's permissions from them.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.auth.infrastructure.postgres_store import PostgresRoleStore
from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore
from mcp_hangar.auth.roles import BUILTIN_ROLES
from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError
from mcp_hangar.domain.value_objects import Permission, Role

OPERATOR = "kubernetes-operator"
# What an older release's row holds: the operator's role without the peer identity.
NARROWED = json.dumps([{"resource_type": "mcp_servers", "action": "write", "resource_id": "*"}])


def _postgres() -> tuple[PostgresRoleStore, MagicMock]:
    connection, cursor = MagicMock(), MagicMock()
    connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)

    class _Factory:
        @contextmanager
        def get_connection(self):
            yield connection

    PostgresRoleStore.__abstractmethods__ = frozenset()
    return PostgresRoleStore(connection_factory=_Factory()), cursor


@pytest.fixture
def sqlite_store(tmp_path):
    path = tmp_path / "auth.db"
    store = SQLiteRoleStore(path)
    store.initialize()
    # The row an older replica's start leaves behind.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE roles SET permissions = ? WHERE name = ?", (NARROWED, OPERATOR))
    store.assign_role("svc:operator", OPERATOR)
    return store


class TestPostgres:
    def test_a_built_in_role_read_by_name_is_this_releases(self) -> None:
        store, cursor = _postgres()
        cursor.fetchone.return_value = (OPERATOR, "older", NARROWED)

        assert store.get_role(OPERATOR) == BUILTIN_ROLES[OPERATOR]

    def test_a_principals_built_in_role_is_this_releases(self) -> None:
        store, cursor = _postgres()
        cursor.fetchall.return_value = [(OPERATOR, "older", NARROWED)]

        assert store.get_roles_for_principal("svc:operator") == [BUILTIN_ROLES[OPERATOR]]

    def test_a_custom_role_is_read_from_its_row(self) -> None:
        store, cursor = _postgres()
        cursor.fetchone.return_value = ("ops", "custom", NARROWED)

        role = store.get_role("ops")

        assert role is not None
        assert role.permissions == frozenset({Permission("mcp_servers", "write", "*")})

    def test_a_custom_role_cannot_take_a_built_in_name(self) -> None:
        store, cursor = _postgres()

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.add_role(Role(name="developer", description="", permissions=frozenset()))

        cursor.execute.assert_not_called()


class TestSQLite:
    def test_a_built_in_role_read_by_name_is_this_releases(self, sqlite_store) -> None:
        assert sqlite_store.get_role(OPERATOR) == BUILTIN_ROLES[OPERATOR]

    def test_a_principals_built_in_role_is_this_releases(self, sqlite_store) -> None:
        assert sqlite_store.get_roles_for_principal("svc:operator") == [BUILTIN_ROLES[OPERATOR]]

    def test_a_custom_role_cannot_take_a_built_in_name(self, sqlite_store) -> None:
        with pytest.raises(CannotModifyBuiltinRoleError):
            sqlite_store.add_role(Role(name="developer", description="", permissions=frozenset()))

        assert sqlite_store.get_role("developer") == BUILTIN_ROLES["developer"]
