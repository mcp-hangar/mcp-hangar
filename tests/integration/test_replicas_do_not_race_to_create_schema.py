"""Against a real PostgreSQL: replicas that boot together all survive it.

Only the server can answer this. `CREATE TABLE IF NOT EXISTS` looks like the
safe spelling and is not concurrency-safe: two sessions can both find the table
absent, both issue the create, and the loser dies on a system-catalog unique
violation --

    duplicate key value violates unique constraint "pg_type_typname_nsp_index"
    DETAIL: Key (typname, typnamespace)=(events, 2200) already exists.

-- which no in-memory stand-in reproduces, because a fake that interprets the
statement agrees with whatever the adapter believed when it was written.

Found by deploying the first HA candidate: `replicas: 3` against an empty
database crashed **two of the three** pods on first boot, three trials out of
three. It self-heals on the restart, so the deployment converges and the whole
episode survives as a restart counter.

Opt-in, like the other `live` tests. See
`tests/integration/test_postgres_tail_does_not_skip.py` for the podman one-liner.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os

import pytest

from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore
from mcp_hangar.infrastructure.persistence.backends.postgresql.management_lease import PostgresManagementLease

pytestmark = pytest.mark.live

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)

#: More than a realistic replica count. The race needs the creators to overlap,
#: and a wider fan-out overlaps more reliably on a loaded runner.
RACERS = 8

TABLES = ("events", "streams", "snapshots", "management_lease")


class _DirectFactory:
    """One connection per call: these creators must genuinely race."""

    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


def _drop_everything() -> None:
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        conn.commit()


@pytest.fixture
def empty_database():
    """A database with none of these tables — the state a first boot finds."""
    _drop_everything()
    yield
    _drop_everything()


def _existing_tables() -> set[str]:
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()",
        )
        return {row[0] for row in cur.fetchall()}


class TestAColdStartWithSeveralReplicas:
    def test_every_creator_survives_the_race(self, empty_database) -> None:
        def create(_: int) -> BaseException | None:
            try:
                PostgresEventStore(_DirectFactory()).initialize()
            except BaseException as exc:  # noqa: BLE001 -- the failure IS the subject
                return exc
            return None

        with ThreadPoolExecutor(max_workers=RACERS) as pool:
            failures = [exc for exc in pool.map(create, range(RACERS)) if exc is not None]

        assert not failures, (
            f"{len(failures)} of {RACERS} concurrent initializers failed; "
            f"first: {type(failures[0]).__name__}: {failures[0]}"
        )
        assert {"events", "streams", "snapshots"} <= _existing_tables()

    def test_the_lease_table_is_created_under_the_same_key(self, empty_database) -> None:
        # A second key would leave this race open while closing the other, which
        # is the same defect with a narrower window. Both creators take the one
        # lock, so mixing them in a single cold start is safe too.
        def create(index: int) -> BaseException | None:
            try:
                if index % 2:
                    PostgresEventStore(_DirectFactory()).initialize()
                else:
                    PostgresManagementLease(_DirectFactory())
            except BaseException as exc:  # noqa: BLE001 -- the failure IS the subject
                return exc
            return None

        with ThreadPoolExecutor(max_workers=RACERS) as pool:
            failures = [exc for exc in pool.map(create, range(RACERS)) if exc is not None]

        assert not failures, f"{len(failures)} of {RACERS} mixed creators failed; first: {failures[0]}"
        assert set(TABLES) <= _existing_tables()

    def test_a_second_start_against_the_full_schema_is_still_free(self, empty_database) -> None:
        # The lock must not turn every restart into a queue: it is taken, found
        # uncontended and released within one transaction. This asserts the
        # ordinary path still works rather than timing it, which a shared runner
        # would make flaky.
        PostgresEventStore(_DirectFactory()).initialize()

        for _ in range(3):
            PostgresEventStore(_DirectFactory()).initialize()

        assert {"events", "streams", "snapshots"} <= _existing_tables()
