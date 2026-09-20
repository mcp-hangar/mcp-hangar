"""Auth writes from two replicas on one PostgreSQL survive each other.

The scenarios are the ones in `tests/unit/test_auth_writes_survive_a_race.py`.
Here they run against two replicas, each with its own store handle, sharing one
database: role assignments and revocations on one principal, and API-key
revocations racing rotations of the same keys.

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

from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore
from tests.unit.test_auth_writes_survive_a_race import (
    a_revocation_that_won_across_a_stale_snapshot_and_a_restart,
    race_key_writes,
    race_role_writes,
    revoke_a_key_across_a_race_and_a_restart,
    revoke_a_role_across_a_race_and_a_restart,
)

pytestmark = pytest.mark.live

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
    name = f"auth_race_{uuid4().hex[:8]}_"
    yield name
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {name}events, {name}streams, {name}snapshots")
        conn.commit()


def _replica(prefix: str) -> PostgresEventStore:
    store = PostgresEventStore(_DirectFactory(), table_prefix=prefix)
    store.initialize()
    return store


def test_role_writes_from_two_replicas_all_land_and_replay(prefix: str) -> None:
    race_role_writes([_replica(prefix), _replica(prefix)])


def test_key_revocations_from_two_replicas_land_whatever_rotates_first(prefix: str) -> None:
    race_key_writes([_replica(prefix), _replica(prefix)])


# Can a revocation lost to the race come back after a restart? A restart here
# is a new store handle on the same database, and a new auth store with no
# snapshot, which is what a new process starts with. The `holds` tests pass
# before the fix too: a revocation that lost the race failed, and was in force
# neither in memory nor in the log.

SNAPSHOT_OR_NOT = pytest.mark.parametrize("cross_a_snapshot", [False, True], ids=["no-snapshot", "crossing-a-snapshot"])


@SNAPSHOT_OR_NOT
def test_a_raced_role_revocation_is_in_force_before_and_after_a_restart(prefix: str, cross_a_snapshot: bool) -> None:
    assert revoke_a_role_across_a_race_and_a_restart(lambda: _replica(prefix), cross_a_snapshot=cross_a_snapshot)


@SNAPSHOT_OR_NOT
def test_what_a_raced_role_revocation_was_told_holds_before_and_after_a_restart(
    prefix: str, cross_a_snapshot: bool
) -> None:
    revoke_a_role_across_a_race_and_a_restart(lambda: _replica(prefix), cross_a_snapshot=cross_a_snapshot)


def test_a_role_revocation_that_won_is_not_undone_by_a_stale_snapshot_or_a_restart(prefix: str) -> None:
    a_revocation_that_won_across_a_stale_snapshot_and_a_restart(lambda: _replica(prefix))


def test_a_raced_key_revocation_is_in_force_before_and_after_a_restart(prefix: str) -> None:
    assert revoke_a_key_across_a_race_and_a_restart(lambda: _replica(prefix))


def test_what_a_raced_key_revocation_was_told_holds_before_and_after_a_restart(prefix: str) -> None:
    revoke_a_key_across_a_race_and_a_restart(lambda: _replica(prefix))
