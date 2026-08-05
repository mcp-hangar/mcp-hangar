"""The two backends are the same kind of thing, and neither is the real one.

SQLite is the standalone default and PostgreSQL is the multi-node answer, but
architecturally they are peers: both are registered factories, both are refused
unless they serve every concern, and each owns its own driver and its own SQL.

These tests are about that symmetry rather than about either implementation.
Each adapter has its own tests; this file exists so the two bundles cannot drift
apart -- one gaining a concern the other lacks is exactly how `tap_store = None`
happened.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_hangar.infrastructure.persistence.backends.postgresql import PostgresqlBackend
from mcp_hangar.infrastructure.persistence.backends.sqlite import SqliteBackend
from mcp_hangar.infrastructure.persistence.registry import REQUIRED_CONCERNS

BACKENDS = (SqliteBackend, PostgresqlBackend)


@pytest.mark.parametrize("backend_cls", BACKENDS, ids=lambda c: c.__name__)
class TestEveryBackendServesEveryConcern:
    def test_all_ten_concerns_are_present(self, backend_cls) -> None:
        missing = [c for c in REQUIRED_CONCERNS if not callable(getattr(backend_cls, c, None))]
        assert missing == [], f"{backend_cls.__name__} does not serve: {missing}"

    def test_each_concern_takes_no_arguments(self, backend_cls) -> None:
        # Callers ask for a concern; they do not configure it. Configuration
        # belongs to the backend, from the block it was handed.
        for concern in REQUIRED_CONCERNS:
            signature = inspect.signature(getattr(backend_cls, concern))
            assert list(signature.parameters) == ["self"], concern

    def test_it_can_be_closed(self, backend_cls) -> None:
        assert callable(getattr(backend_cls, "close", None))


class TestNeitherBackendKnowsTheOther:
    def test_the_sqlite_backend_mentions_no_postgres(self) -> None:
        source = inspect.getsource(SqliteBackend)
        assert "psycopg" not in source.lower()
        assert "postgres" not in source.lower().replace("postgresql backend", "")

    def test_the_postgres_backend_opens_no_connection_itself(self) -> None:
        # It asks the shared factory. An adapter that imports psycopg2 directly
        # is a second place that knows the driver, which is how two pools and
        # two configurations appear against one database.
        source = inspect.getsource(PostgresqlBackend)
        assert "import psycopg2" not in source

    def test_no_adapter_carries_a_dialect_branch(self) -> None:
        # The property that makes these separate implementations rather than one
        # implementation with two modes.
        import pathlib

        pg_dir = pathlib.Path(__file__).resolve().parents[1] / (
            "../src/mcp_hangar/infrastructure/persistence/backends/postgresql"
        )
        for module in sorted(pg_dir.resolve().glob("*.py")):
            text = module.read_text(encoding="utf-8")
            assert "sqlite3" not in text, f"{module.name} reaches for sqlite3"
            assert "driver ==" not in text, f"{module.name} branches on a driver"


class TestTheBundlesCannotDriftApart:
    def test_they_expose_exactly_the_same_concerns(self) -> None:
        def concerns(cls: type) -> set[str]:
            return {
                name
                for name in dir(cls)
                if not name.startswith("_") and name != "close" and callable(getattr(cls, name))
            }

        assert concerns(SqliteBackend) == concerns(PostgresqlBackend)
