"""When a backend is selected, the consumers take their storage from it.

The registry can be perfect and still buy nothing if every subsystem keeps
building its own store beside it. That is the failure this file exists to catch,
and it is the one this codebase produces most reliably: something correct, wired
halfway, and silent about the half that is missing.

So these tests assert the wiring rather than the mechanism -- that auth,
approvals and sagas ask the selected backend, and that with no backend selected
they build their own exactly as before.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.infrastructure.persistence.registry import REQUIRED_CONCERNS


class _Backend:
    """A backend that hands out labelled strings, so provenance is visible."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def close(self) -> None:
        return None


def _concern(name: str):
    def method(self: _Backend) -> str:
        self.asked.append(name)
        return f"from-backend:{name}"

    return method


for _name in REQUIRED_CONCERNS:
    setattr(_Backend, _name, _concern(_name))


@pytest.fixture
def selected(monkeypatch):
    """Install a backend the way bootstrap does, and clean up after."""
    from mcp_hangar.server.bootstrap import composition

    backend = _Backend()
    monkeypatch.setattr(composition, "_persistence_backend", backend)
    yield backend
    monkeypatch.setattr(composition, "_persistence_backend", None)


class TestAuthTakesAllThreeStoresFromTheBackend:
    def test_it_asks_for_every_store_together(self, selected) -> None:
        # Together is the point. The old code chose them per driver and could
        # return None for the policy store, which silently switched off policy
        # management and its startup replay.
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig

        api_keys, roles, policies = _create_storage_backends(AuthConfig(), persistence_backend=selected)

        assert api_keys == "from-backend:api_key_store"
        assert roles == "from-backend:role_store"
        assert policies == "from-backend:tool_access_policy_store"

    def test_the_policy_store_is_never_none(self, selected) -> None:
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig

        _, _, policies = _create_storage_backends(AuthConfig(), persistence_backend=selected)

        assert policies is not None

    def test_without_a_backend_it_configures_itself_as_before(self) -> None:
        # The compatibility path: 2.4.0 is released, and an existing
        # configuration must keep doing what it did.
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig

        api_keys, roles, _ = _create_storage_backends(AuthConfig())

        assert type(api_keys).__name__ == "InMemoryApiKeyStore"
        assert type(roles).__name__ == "InMemoryRoleStore"


class TestApprovalsTakeTheirRepositoryFromTheBackend:
    def test_the_repository_comes_from_the_backend(self, selected) -> None:
        from mcp_hangar.server.bootstrap.components import _approval_repository_from_backend

        assert _approval_repository_from_backend() == "from-backend:approval_repository"

    def test_without_a_backend_there_is_nothing_to_hand_over(self) -> None:
        from mcp_hangar.server.bootstrap.components import _approval_repository_from_backend

        assert _approval_repository_from_backend() is None

    def test_a_supplied_repository_is_used_instead_of_building_one(self) -> None:
        # `bootstrap_approvals` builds SQLite from a Database when it gets
        # nothing. Passing one has to win, or the backend's choice is ignored.
        from mcp_hangar.approvals.bootstrap import bootstrap_approvals

        sentinel = object()
        service = bootstrap_approvals(database=None, event_bus=None, config={}, repository=sentinel)

        assert service._repository is sentinel

    def test_without_one_it_builds_the_sqlite_repository(self, tmp_path) -> None:
        # The other side of that branch, and the compatibility path: a
        # deployment that selected no backend still gets a working gate.
        from mcp_hangar.approvals.bootstrap import bootstrap_approvals
        from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig

        database = Database(DatabaseConfig(path=str(tmp_path / "approvals.db")))
        service = bootstrap_approvals(database=database, event_bus=None, config={})

        assert type(service._repository).__name__ == "SqliteApprovalRepository"


class TestSagasTakeTheirStoreFromTheBackend:
    def test_the_store_comes_from_the_backend(self, selected) -> None:
        from mcp_hangar.server.bootstrap.cqrs import _create_saga_state_store

        store = _create_saga_state_store({"event_store": {"driver": "memory"}})

        assert store == "from-backend:saga_state_store"

    def test_the_backend_wins_over_the_event_store_driver(self, selected) -> None:
        # The compatibility path keys saga state off the *event store's* driver.
        # That cross-subsystem coupling is exactly what one storage decision
        # removes, so a selected backend must not be overridden by it.
        from mcp_hangar.server.bootstrap.cqrs import _create_saga_state_store

        assert _create_saga_state_store({"event_store": {"driver": "sqlite"}}) == "from-backend:saga_state_store"

    def test_without_a_backend_the_old_rule_still_applies(self) -> None:
        from mcp_hangar.server.bootstrap.cqrs import _create_saga_state_store

        store = _create_saga_state_store({"event_store": {"driver": "memory"}})

        assert type(store).__name__ == "NullSagaStateStore"


class TestTheEventLogAndItsMarkComeFromTheBackend:
    def test_both_are_installed_from_it(self, selected) -> None:
        from types import SimpleNamespace

        from mcp_hangar.server.bootstrap.event_store import init_event_store

        installed: dict[str, Any] = {}
        bus = SimpleNamespace(
            set_event_store=lambda s: installed.__setitem__("store", s),
            set_dispatch_checkpoint=lambda c: installed.__setitem__("checkpoint", c),
        )

        init_event_store(SimpleNamespace(event_bus=bus), {"persistence": {"backend": "sqlite"}})

        assert installed["store"] == "from-backend:event_store"
        assert installed["checkpoint"] == "from-backend:dispatch_checkpoint"


class TestTheConfigAndAuditRepositoriesComeFromTheBackend:
    def test_both_are_taken_from_it(self, selected) -> None:
        # These are built during Runtime construction, which is why the backend
        # has to be selected before the runtime is asked for rather than after.
        from mcp_hangar.bootstrap.runtime import create_runtime

        runtime = create_runtime(persistence_backend=selected)

        assert runtime.config_repository == "from-backend:config_repository"
        assert runtime.audit_repository == "from-backend:audit_repository"

    def test_no_sqlite_database_handle_is_opened(self, selected) -> None:
        # The handle exists to create the SQLite schema. A backend's adapters
        # create their own, so opening one would be a second database beside the
        # selected one -- the split this whole change removes.
        from mcp_hangar.bootstrap.runtime import create_runtime

        runtime = create_runtime(persistence_backend=selected)

        assert runtime.database is None

    def test_recovery_still_runs(self, selected) -> None:
        # Recovery replays configurations into the fleet on startup. Losing it
        # while moving repositories would be a silent regression: the gateway
        # would start empty and look fine.
        from mcp_hangar.bootstrap.runtime import create_runtime

        runtime = create_runtime(persistence_backend=selected)

        assert runtime.recovery_service is not None
        assert runtime.persistence_config is not None and runtime.persistence_config.enabled

    def test_without_a_backend_the_previous_shape_is_unchanged(self) -> None:
        from mcp_hangar.bootstrap.runtime import create_runtime

        runtime = create_runtime()

        assert type(runtime.config_repository).__name__ == "InMemoryMcpServerConfigRepository"
        assert runtime.database is None

    def test_the_runtime_is_still_frozen(self, selected) -> None:
        # It is frozen on purpose: assembled once, not written to afterwards.
        # An earlier version of this work assigned the backend onto it and broke
        # bootstrap for every configuration.
        import dataclasses

        import pytest as _pytest

        from mcp_hangar.bootstrap.runtime import create_runtime

        runtime = create_runtime(persistence_backend=selected)

        with _pytest.raises(dataclasses.FrozenInstanceError):
            runtime.config_repository = None  # type: ignore[misc]
