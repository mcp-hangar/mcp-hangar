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
        # This test used to name the event store and only the event store:
        #
        #     assert "EventStore" not in line
        #
        # Three adapters with the identical shape -- api keys, roles and
        # tool-access policies -- were added and handed out uninitialised right
        # past it, because their names are not "EventStore". A guard written
        # around one instance of a shape does not guard the shape.
        #
        # So the rule is stated the other way round now: every concern either
        # initialises what it returns, or is named below as self-initialising.
        # Adding an adapter forces that decision instead of inheriting silence.
        import inspect

        from mcp_hangar.infrastructure.persistence.backends import postgresql
        from mcp_hangar.infrastructure.persistence.registry import REQUIRED_CONCERNS

        #: Adapters that create their schema in ``__init__`` and therefore need
        #: no second step. Each entry is a claim you can check by reading it.
        SELF_INITIALISING = {
            "dispatch_checkpoint",
            "config_repository",
            "audit_repository",
            "saga_state_store",
            "approval_repository",
            "metrics_history_store",
            "management_lease",
        }

        for concern in REQUIRED_CONCERNS:
            factory = getattr(postgresql.PostgresqlBackend, concern, None)
            if factory is None:
                continue
            source = inspect.getsource(factory)
            if concern in SELF_INITIALISING:
                continue
            assert "initialize()" in source, (
                f"{concern} is handed out without initialize(); either call it, "
                f"or add {concern!r} to SELF_INITIALISING once you have checked "
                "that its constructor creates the schema"
            )


class TestASqliteBackendHandsOutUsableAuthAdapters:
    """The same defect, on the backend where it needs no server to show.

    `persistence.backend: sqlite` failed identically -- `no such table: roles`
    at startup -- because `SQLiteApiKeyStore` and `SQLiteRoleStore` also keep
    schema creation in `initialize()`. These assert on behaviour rather than on
    source, which is the stronger form and only possible here because SQLite
    needs nothing running.
    """

    def test_a_role_can_be_assigned_on_a_freshly_built_store(self, backend) -> None:
        store = backend.role_store()

        store.assign_role("service:probe", "admin", scope="global")

        assert any(r.name == "admin" for r in store.get_roles_for_principal("service:probe"))

    def test_the_builtin_roles_are_seeded(self, backend) -> None:
        # `assign_role` verifies the role exists before inserting, so an
        # unseeded store fails even when the tables are there.
        assert backend.role_store().get_role("provider-admin") is not None

    def test_an_api_key_can_be_created_on_a_freshly_built_store(self, backend) -> None:
        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        store = backend.api_key_store()

        raw = store.create_key(principal_id="service:probe", name="probe")

        assert store.get_principal_for_key(ApiKeyAuthenticator._hash_key(raw)) is not None
