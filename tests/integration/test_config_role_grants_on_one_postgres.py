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

from mcp_hangar.auth.infrastructure.postgres_store import PostgresRoleStore
from tests.unit.test_config_role_assignments_are_reconciled import SCENARIOS, OpenStore

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
def open_store() -> Iterator[OpenStore]:
    """Tables of this test's own, dropped afterwards."""
    prefix = f"cfg_grants_{uuid4().hex[:8]}_"

    def open_postgres() -> PostgresRoleStore:
        store = PostgresRoleStore(_DirectFactory(), table_prefix=prefix)
        store.initialize()
        return store

    yield open_postgres
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {prefix}role_assignments, {prefix}roles")
        conn.commit()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_config_role_assignments_are_reconciled_on_postgres(open_store: OpenStore, scenario) -> None:
    scenario(open_store)
