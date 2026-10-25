"""A grant removed from ``auth.role_assignments`` is revoked at the next boot, on PostgreSQL.

The scenarios are the ones in `tests/unit/test_config_role_assignments_are_reconciled.py`.
A restart is a new store handle on the same tables.

Opt-in, like the other `live` tests: set `HANGAR_TEST_POSTGRES_DSN`. See
`tests/integration/test_postgres_tail_does_not_skip.py` for the one-liner that
starts a server.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest

from mcp_hangar.auth.infrastructure.postgres_store import PostgresApiKeyStore, PostgresRoleStore
from tests.unit.test_config_role_assignments_are_reconciled import BOOTSTRAP_SCENARIOS, SCENARIOS, OpenStore

pytestmark = [pytest.mark.live, pytest.mark.security]

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)


class _DirectFactory:
    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def prefix() -> Iterator[str]:
    """Tables of this test's own, dropped afterwards."""
    name = f"cfg_grants_{uuid4().hex[:8]}_"
    yield name
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"DROP TABLE IF EXISTS {name}initial_admin_bootstrap, {name}api_keys, {name}role_assignments, {name}roles"
        )
        conn.commit()


@pytest.fixture
def open_store(prefix: str) -> OpenStore:
    """A new store handle on this test's tables, as a restarted process opens."""

    def open_postgres() -> PostgresRoleStore:
        store = PostgresRoleStore(_DirectFactory(), table_prefix=prefix)
        store.initialize()
        return store

    return open_postgres


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_config_role_assignments_are_reconciled_on_postgres(open_store: OpenStore, scenario) -> None:
    scenario(open_store)


@pytest.mark.parametrize("scenario", BOOTSTRAP_SCENARIOS, ids=lambda f: f.__name__)
def test_the_bootstrap_admin_owns_its_grant_on_postgres(prefix: str, scenario) -> None:
    role_store = PostgresRoleStore(_DirectFactory(), table_prefix=prefix)
    role_store.initialize()
    key_store = PostgresApiKeyStore(_DirectFactory(), table_prefix=prefix)
    key_store.initialize()
    scenario(role_store, key_store)
