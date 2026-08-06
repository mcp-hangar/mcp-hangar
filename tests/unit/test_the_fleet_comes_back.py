"""Bootstrap restores the fleet it wrote down, and can write it at all.

Two defects, one of them mine. #794 made registration persist the fleet, and
its test called `RecoveryService` directly, which passed while:

- **nothing called recovery in production.** `recover_mcp_servers` had exactly
  one caller, `bootstrap.runtime.initialize_runtime`, and that function has no
  callers at all. The snapshot was written on every registration and never read.
- **nothing created the schema.** `Database.initialize()` had the same single
  dead caller, so on `persistence.backend: sqlite` the very first registration
  failed with `no such table: mcp_server_configs` -- invisible before #794,
  because until then nothing wrote to that table either.

The difference between the test that passed and the tests here is the
difference between testing a component and testing that it is plugged in. These
go through the real entry points.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.recovery_service import RecoveryService
from mcp_hangar.infrastructure.persistence.registry import create_backend
from mcp_hangar.server.bootstrap.persistence import restore_persisted_fleet


def _bootstrap_function():
    """The `bootstrap()` function, not the package of the same name.

    `mcp_hangar.server.bootstrap` resolves to the re-exported function, so the
    module has to be fetched from `sys.modules`.
    """
    import sys

    import mcp_hangar.server.bootstrap  # noqa: F401 -- imported for its side effect on sys.modules

    return sys.modules["mcp_hangar.server.bootstrap"].bootstrap


def _snapshot(mcp_server_id: str) -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"])


class TestTheSchemaExistsWhenSomethingWritesToIt:
    def test_a_registration_against_a_fresh_backend_succeeds(self, tmp_path) -> None:
        # This is the regression #794 introduced and did not catch: it made the
        # write mandatory and loud, on a path where the tables were never
        # created. `POST /api/mcp_servers` returned a PersistenceError.
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            RepositoryFleetWriter(backend.config_repository()).save(_snapshot("math"))
        finally:
            backend.close()

    def test_reading_a_fresh_backend_does_not_raise_either(self, tmp_path) -> None:
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            from mcp_hangar.infrastructure.async_bridge import BackgroundLoop

            loop = BackgroundLoop()
            try:
                assert loop.run(backend.config_repository().get_all(), 15.0) == []
            finally:
                loop.close()
        finally:
            backend.close()

    def test_the_schema_arrives_with_the_connection_not_with_a_call(self) -> None:
        # Stated as a test because the fix is the *removal* of an ordering
        # question. Anything that can get a connection can rely on the tables
        # being there, so no future caller has to know to initialize first.
        import inspect

        from mcp_hangar.infrastructure.persistence.database import Database

        assert "self.initialize()" in inspect.getsource(Database.connection)


class TestBootstrapReadsBackWhatItWrote:
    def test_a_persisted_server_is_restored(self, tmp_path) -> None:
        # The round trip through the real entry point. Write with the writer the
        # command handler uses, then restore with the function bootstrap calls.
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            RepositoryFleetWriter(backend.config_repository()).save(_snapshot("math"))

            fleet = InMemoryMcpServerRepository()
            restored = restore_persisted_fleet(_runtime_with(backend, fleet))

            assert restored == 1
            assert fleet.get("math") is not None
        finally:
            backend.close()

    def test_bootstrap_actually_calls_it(self) -> None:
        # The missing half. A component that works and is never called looks
        # exactly like one that does not exist.
        import inspect

        assert "restore_persisted_fleet(runtime)" in inspect.getsource(_bootstrap_function())

    def test_it_runs_after_the_event_store_is_installed(self) -> None:
        # Each restored server replays its own stream to recover the lifecycle
        # state it had. Restoring before the store exists brings every server
        # back COLD -- a circuit breaker reset handed out by restarting.
        import inspect

        source = inspect.getsource(_bootstrap_function())

        assert source.index("init_event_store(runtime") < source.index("restore_persisted_fleet(runtime)")

    def test_nothing_is_restored_without_persistence(self) -> None:
        runtime = SimpleNamespace(recovery_service=None, persistence_config=None)

        assert restore_persisted_fleet(runtime) == 0

    def test_auto_recover_off_means_nothing_is_restored(self, tmp_path) -> None:
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            RepositoryFleetWriter(backend.config_repository()).save(_snapshot("math"))
            fleet = InMemoryMcpServerRepository()

            runtime = _runtime_with(backend, fleet, auto_recover=False)

            assert restore_persisted_fleet(runtime) == 0
            assert fleet.get("math") is None
        finally:
            backend.close()

    def test_an_unreadable_snapshot_does_not_stop_the_boot(self) -> None:
        # The servers declared in config.yaml are already loaded and working by
        # this point. Refusing to start would turn a bad row into an outage for
        # them.
        class _Exploding:
            async def recover_mcp_servers(self):
                raise RuntimeError("the database is on fire")

        runtime = SimpleNamespace(
            recovery_service=_Exploding(),
            persistence_config=SimpleNamespace(enabled=True, auto_recover=True),
        )

        assert restore_persisted_fleet(runtime) == 0


@pytest.mark.asyncio
class TestConfigurationWinsOverTheSnapshot:
    async def test_a_server_declared_in_config_is_not_overwritten(self, tmp_path) -> None:
        # config.yaml is the operator's live intent; the snapshot is a record of
        # what was true last time. An operator who edits the file and restarts
        # must not have the edit reverted by a row.
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            await backend.config_repository().save(
                McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess", description="from the snapshot")
            )

            fleet = InMemoryMcpServerRepository()
            from mcp_hangar.domain.model import McpServer

            fleet.add("math", McpServer(mcp_server_id="math", mode="subprocess", description="from config.yaml"))

            await RecoveryService(
                database=None,
                mcp_server_repository=fleet,
                config_repository=backend.config_repository(),
                audit_repository=backend.audit_repository(),
            ).recover_mcp_servers()

            assert fleet.get("math").description == "from config.yaml"
        finally:
            backend.close()

    async def test_a_server_only_in_the_snapshot_is_restored_alongside(self, tmp_path) -> None:
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            await backend.config_repository().save(_snapshot("registered-at-runtime"))

            fleet = InMemoryMcpServerRepository()
            from mcp_hangar.domain.model import McpServer

            fleet.add("from-config", McpServer(mcp_server_id="from-config", mode="subprocess"))

            recovered = await RecoveryService(
                database=None,
                mcp_server_repository=fleet,
                config_repository=backend.config_repository(),
                audit_repository=backend.audit_repository(),
            ).recover_mcp_servers()

            assert recovered == ["registered-at-runtime"]
            assert sorted(fleet.get_all_ids()) == ["from-config", "registered-at-runtime"]
        finally:
            backend.close()


def _runtime_with(backend, fleet, *, auto_recover: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        recovery_service=RecoveryService(
            database=None,
            mcp_server_repository=fleet,
            config_repository=backend.config_repository(),
            audit_repository=backend.audit_repository(),
        ),
        persistence_config=SimpleNamespace(enabled=True, auto_recover=auto_recover),
    )
