"""Every piece of PostgreSQL DDL takes the lock, including the next one written.

`CREATE TABLE IF NOT EXISTS` reads as the safe spelling and is not: two sessions
can both find the table absent and the loser dies on a system-catalog unique
violation. Sequentially it is idempotent, which is why it survived a year of
tests and every single-gateway deployment -- and a replica set is precisely a
set of processes that start at the same instant against the same database.

The live proof is `tests/integration/test_replicas_do_not_race_to_create_schema.py`.
This file guards the thing a live test cannot: that the *next* adapter added to
this backend does not quietly reintroduce the race. The rule is mechanical, so
the check is too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_hangar.infrastructure.persistence.database_common import (
    POSTGRES_SCHEMA_LOCK_KEY,
    postgres_ddl,
)

BACKEND = Path(__file__).resolve().parents[2] / "src/mcp_hangar/infrastructure/persistence/backends/postgresql"


class TestTheLockRidesWithTheStatement:
    def test_the_ddl_is_prefixed_with_the_advisory_lock(self) -> None:
        wrapped = postgres_ddl("CREATE TABLE IF NOT EXISTS thing (id TEXT);")

        assert wrapped.startswith(f"SELECT pg_advisory_xact_lock({POSTGRES_SCHEMA_LOCK_KEY});")
        assert "CREATE TABLE IF NOT EXISTS thing" in wrapped

    def test_it_is_a_transaction_lock_not_a_session_one(self) -> None:
        # `pg_advisory_lock` would need an unlock, and a DDL path that raises
        # between the two would hold it until the connection is recycled --
        # which, from a pool, can be a very long time. The xact form is
        # released by the commit or the rollback, whichever happens.
        assert "pg_advisory_xact_lock" in postgres_ddl("SELECT 1")
        assert "pg_advisory_unlock" not in postgres_ddl("SELECT 1")

    def test_one_key_for_all_of_it(self) -> None:
        # Two keys would let the events table and the lease table be created
        # concurrently by different replicas, which is the same race again with
        # a smaller window. It also has to fit a signed bigint.
        assert 0 < POSTGRES_SCHEMA_LOCK_KEY < 2**63


def _modules_that_create_tables() -> list[Path]:
    return sorted(p for p in BACKEND.glob("*.py") if "CREATE TABLE" in p.read_text())


class TestNoAdapterCreatesATableUnserialized:
    def test_the_scan_finds_the_adapters_it_is_meant_to(self) -> None:
        # A guard whose subject list silently emptied would pass forever.
        found = {p.name for p in _modules_that_create_tables()}

        assert {"event_store.py", "management_lease.py", "config_repository.py"} <= found
        assert len(found) >= 8

    @pytest.mark.parametrize("module", _modules_that_create_tables(), ids=lambda p: p.name)
    def test_it_serializes_its_ddl(self, module: Path) -> None:
        source = module.read_text()

        assert "postgres_ddl" in source or "postgres_schema_lock" in source, (
            f"{module.name} creates a table without taking the DDL lock. "
            "`CREATE TABLE IF NOT EXISTS` is not concurrency-safe in PostgreSQL: "
            "wrap the statement in `postgres_ddl(...)`, or hold "
            "`postgres_schema_lock(factory)` if the DDL runs on a connection you do not own."
        )
