"""Every adapter a backend hands out can be used immediately.

ADR-019 says a backend's adapters create their own schema. Seven of the eight
PostgreSQL adapters do it when they are built. The event store kept it as a
separate `initialize()` and **nothing called it**, so a gateway on
`persistence.backend: postgresql` ran with no `events` table.

Nothing said so, because nothing read the log during startup: the delivery sweep
is standalone-only now, and the first append had not happened yet. What did
happen was the tailer reporting `relation "events" does not exist` every two
seconds, into a log nobody was watching -- found by deploying two replicas and
looking, not by a test.

So this file asserts the property rather than the call: **whatever a backend
returns is usable without a second step**, for every concern it provides.
"""

from __future__ import annotations

import pytest

from mcp_hangar.infrastructure.persistence.registry import REQUIRED_CONCERNS, create_backend


@pytest.fixture
def backend(tmp_path):
    made = create_backend("sqlite", {"data_dir": str(tmp_path)})
    yield made
    made.close()


class TestASqliteBackendHandsOutUsableAdapters:
    def test_the_event_store_can_be_appended_to_and_read_back(self, backend) -> None:
        from mcp_hangar.domain.contracts.event_store import BEGINNING
        from mcp_hangar.domain.events import McpServerStarted

        store = backend.event_store()
        store.append(
            "mcp_server:math",
            [McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=1, startup_duration_ms=1.0)],
            expected_version=-1,
        )

        assert len(store.read_since(BEGINNING)[0]) == 1

    def test_every_concern_is_reachable_without_a_second_step(self, backend) -> None:
        # The completeness rule from ADR-019 says a backend provides all of
        # them; this says each one it provides is ready to use. Missing an
        # `initialize()` is invisible to the first rule and fatal to the second.
        for concern in REQUIRED_CONCERNS:
            assert getattr(backend, concern)() is not None, concern


class TestThePostgresqlBackendInitialisesItsEventStore:
    def test_the_factory_creates_the_schema(self) -> None:
        # Asserted on the factory rather than on a live server, because the
        # failure is the *absence* of a call: a test against a database that
        # already had the tables would pass either way.
        import inspect

        from mcp_hangar.infrastructure.persistence.backends import postgresql

        source = inspect.getsource(postgresql.PostgresqlBackend.event_store)

        assert "store.initialize()" in source

    def test_no_adapter_is_handed_out_uninitialised(self) -> None:
        # Every other adapter in the backend creates its schema in its
        # constructor. The event store is the one that does not, so the backend
        # has to do it -- and if a future adapter follows the same pattern, this
        # is where the omission shows up.
        import inspect

        from mcp_hangar.infrastructure.persistence.backends import postgresql

        source = inspect.getsource(postgresql)
        builders = [line for line in source.splitlines() if line.strip().startswith("return Postgres")]

        for line in builders:
            assert "EventStore" not in line, (
                "the event store must be initialised before it is returned; "
                f"this line hands it out directly: {line.strip()}"
            )
