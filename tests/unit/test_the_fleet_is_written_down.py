"""A server registered at runtime is still there after a restart.

`RecoveryService.recover_mcp_servers` reads a table of configurations on every
start. Nothing wrote it: `save_mcp_server_config` had no caller outside a unit
test, so a server registered through the API or found by discovery lived in
memory and was gone when the process ended. The event log recorded that the
registration happened, which is what made the trail look complete while the
fleet was not.

The first test here is the round trip -- register, then recover into a fresh
fleet -- because that is the behaviour that was missing, and asserting that a
method got called would have passed before and after.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import (
    CreateMcpServerCommand,
    DeleteMcpServerCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    CreateMcpServerHandler,
    DeleteMcpServerHandler,
    UpdateMcpServerHandler,
)
from mcp_hangar.domain.contracts.fleet import IFleetWriter
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.infrastructure.persistence.config_repository import InMemoryMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence.recovery_service import RecoveryService


class _SilentBus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
        return 0


class _RefusingWriter(IFleetWriter):
    def save(self, snapshot: McpServerConfigSnapshot) -> None:
        raise RuntimeError("the database is unreachable")

    def delete(self, mcp_server_id: str) -> None:
        raise RuntimeError("the database is unreachable")


def _create(mcp_server_id: str = "math", **overrides: Any) -> CreateMcpServerCommand:
    fields: dict[str, Any] = {
        "mcp_server_id": mcp_server_id,
        "mode": "subprocess",
        "command": ["python", "-m", "server"],
        "description": "adds numbers",
        "source": "api",
    }
    fields.update(overrides)
    return CreateMcpServerCommand(**fields)


@pytest.mark.asyncio
class TestARegistrationSurvivesTheProcess:
    async def test_a_registered_server_comes_back_after_a_restart(self) -> None:
        # The whole defect, end to end. Register into one fleet, then recover
        # into an empty one -- which is what the next process does.
        configs = InMemoryMcpServerConfigRepository()
        fleet = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(
            repository=fleet, event_bus=_SilentBus(), fleet_writer=RepositoryFleetWriter(configs)
        )

        handler.handle(_create("math"))

        after_restart = InMemoryMcpServerRepository()
        recovered = await RecoveryService(
            database=None,
            mcp_server_repository=after_restart,
            config_repository=configs,
            audit_repository=_NullAudit(),
        ).recover_mcp_servers()

        assert recovered == ["math"]
        assert after_restart.get("math") is not None

    async def test_what_comes_back_is_what_was_registered(self) -> None:
        # A record that loses fields is its own kind of silence: the server
        # returns, configured as something slightly different.
        configs = InMemoryMcpServerConfigRepository()
        handler = CreateMcpServerHandler(
            repository=InMemoryMcpServerRepository(),
            event_bus=_SilentBus(),
            fleet_writer=RepositoryFleetWriter(configs),
        )

        handler.handle(_create("math", command=["python", "-m", "server"], description="adds numbers"))

        stored = await configs.get("math")
        assert stored is not None
        assert (stored.mode, stored.command, stored.description) == (
            "subprocess",
            ["python", "-m", "server"],
            "adds numbers",
        )

    async def test_an_update_is_not_reverted_by_a_restart(self) -> None:
        configs = InMemoryMcpServerConfigRepository()
        fleet = InMemoryMcpServerRepository()
        writer = RepositoryFleetWriter(configs)
        CreateMcpServerHandler(repository=fleet, event_bus=_SilentBus(), fleet_writer=writer).handle(_create("math"))

        UpdateMcpServerHandler(repository=fleet, event_bus=_SilentBus(), fleet_writer=writer).handle(
            UpdateMcpServerCommand(mcp_server_id="math", description="now does subtraction too", source="api")
        )

        stored = await configs.get("math")
        assert stored is not None and stored.description == "now does subtraction too"

    async def test_a_deleted_server_does_not_come_back(self) -> None:
        # The other direction, and the worse one: a row left behind resurrects
        # a server an operator deliberately removed.
        configs = InMemoryMcpServerConfigRepository()
        fleet = InMemoryMcpServerRepository()
        writer = RepositoryFleetWriter(configs)
        CreateMcpServerHandler(repository=fleet, event_bus=_SilentBus(), fleet_writer=writer).handle(_create("math"))

        DeleteMcpServerHandler(repository=fleet, event_bus=_SilentBus(), fleet_writer=writer).handle(
            DeleteMcpServerCommand(mcp_server_id="math", source="api")
        )

        after_restart = InMemoryMcpServerRepository()
        recovered = await RecoveryService(
            database=None,
            mcp_server_repository=after_restart,
            config_repository=configs,
            audit_repository=_NullAudit(),
        ).recover_mcp_servers()

        assert recovered == []


class TestAFailedWriteIsNotASuccessfulRegistration:
    def test_the_command_fails_rather_than_reporting_created(self) -> None:
        # Fire-and-forget was available and is what the existing async executor
        # does. It would answer "created" here and lose the server at the next
        # restart -- the same defect, reported as success.
        handler = CreateMcpServerHandler(
            repository=InMemoryMcpServerRepository(), event_bus=_SilentBus(), fleet_writer=_RefusingWriter()
        )

        with pytest.raises(RuntimeError):
            handler.handle(_create("math"))

    def test_nothing_joins_the_fleet_when_the_write_fails(self) -> None:
        # Recorded before it joins the fleet, so a failure leaves nothing
        # behind rather than a running server nobody wrote down.
        fleet = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(repository=fleet, event_bus=_SilentBus(), fleet_writer=_RefusingWriter())

        with pytest.raises(RuntimeError):
            handler.handle(_create("math"))

        assert fleet.get("math") is None

    def test_no_event_is_published_for_a_registration_that_did_not_happen(self) -> None:
        published: list[Any] = []

        class _Bus(_SilentBus):
            def publish(self, event: Any) -> None:
                published.append(event)

        handler = CreateMcpServerHandler(
            repository=InMemoryMcpServerRepository(), event_bus=_Bus(), fleet_writer=_RefusingWriter()
        )

        with pytest.raises(RuntimeError):
            handler.handle(_create("math"))

        assert published == []

    def test_a_failed_delete_leaves_the_server_in_the_fleet(self) -> None:
        fleet = InMemoryMcpServerRepository()
        CreateMcpServerHandler(repository=fleet, event_bus=_SilentBus()).handle(_create("math"))

        with pytest.raises(RuntimeError):
            DeleteMcpServerHandler(repository=fleet, event_bus=_SilentBus(), fleet_writer=_RefusingWriter()).handle(
                DeleteMcpServerCommand(mcp_server_id="math", source="api")
            )

        assert fleet.get("math") is not None


class TestWithoutAWriterNothingChanges:
    def test_registration_still_works_with_no_durable_storage(self) -> None:
        # 2.4.0 is released and most deployments select no storage backend.
        # They keep the previous behaviour: in memory, gone on restart.
        fleet = InMemoryMcpServerRepository()

        result = CreateMcpServerHandler(repository=fleet, event_bus=_SilentBus()).handle(_create("math"))

        assert result == {"mcp_server_id": "math", "created": True}
        assert fleet.get("math") is not None

    def test_the_in_memory_repository_is_not_treated_as_storage(self) -> None:
        # Writing to it would be worse than not writing: /api/config would
        # report the server as persisted, and it would still be gone.
        from mcp_hangar.server.bootstrap.cqrs import _fleet_writer

        runtime = type("_R", (), {"config_repository": InMemoryMcpServerConfigRepository()})()

        assert _fleet_writer(runtime) is None

    def test_a_backend_repository_does_get_a_writer(self) -> None:
        from mcp_hangar.server.bootstrap.cqrs import _fleet_writer

        runtime = type("_R", (), {"config_repository": _RecordingRepository()})()

        assert _fleet_writer(runtime) is not None

    def test_no_repository_at_all_is_not_an_error(self) -> None:
        from mcp_hangar.server.bootstrap.cqrs import _fleet_writer

        assert _fleet_writer(type("_R", (), {"config_repository": None})()) is None


class TestTheSyncWriterOverAnAsyncRepository:
    def test_it_waits_for_the_write_rather_than_scheduling_it(self) -> None:
        # A registration that returns before its row exists is the failure this
        # writer is for. The read happens immediately after the call returns.
        repository = _RecordingRepository()
        RepositoryFleetWriter(repository).save(McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess"))

        assert [snapshot.mcp_server_id for snapshot in repository.saved] == ["math"]

    def test_a_failure_in_the_repository_reaches_the_caller(self) -> None:
        class _Failing:
            async def save(self, snapshot: Any) -> None:
                raise RuntimeError("disk full")

            async def delete(self, mcp_server_id: str) -> bool:
                raise RuntimeError("disk full")

        with pytest.raises(RuntimeError, match="disk full"):
            RepositoryFleetWriter(_Failing()).save(McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess"))

    def test_the_loop_survives_more_than_one_write(self) -> None:
        # It is reused across calls on purpose: aiosqlite starts a thread per
        # connection, and a loop torn down after each write would close
        # connections the repository still means to use.
        repository = _RecordingRepository()
        writer = RepositoryFleetWriter(repository)

        writer.save(McpServerConfigSnapshot(mcp_server_id="a", mode="subprocess"))
        writer.save(McpServerConfigSnapshot(mcp_server_id="b", mode="subprocess"))
        writer.delete("a")

        assert [snapshot.mcp_server_id for snapshot in repository.saved] == ["a", "b"]
        assert repository.deleted == ["a"]
        writer.close()

    def test_the_writers_thread_does_not_keep_the_process_alive(self) -> None:
        # Found by running these tests: they all passed in under a second and
        # the pytest process then never exited. A `ThreadPoolExecutor` gives a
        # non-daemon thread, CPython joins those *before* it runs `atexit`, and
        # the handler that would have stopped the loop never ran. In production
        # that is a gateway that finishes its shutdown and hangs.
        import threading as _threading

        writer = RepositoryFleetWriter(_RecordingRepository())
        writer.save(McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess"))

        thread = next(t for t in _threading.enumerate() if t.name == "fleet-writer")
        assert thread.daemon, "a non-daemon writer thread hangs the process at exit"
        writer.close()

    def test_a_write_that_hangs_becomes_a_failure_not_a_hang(self) -> None:
        import asyncio as _asyncio

        class _Hanging:
            async def save(self, snapshot: Any) -> None:
                await _asyncio.sleep(30)

        from concurrent.futures import TimeoutError as FutureTimeout

        writer = RepositoryFleetWriter(_Hanging(), timeout_s=0.2)
        with pytest.raises(FutureTimeout):
            writer.save(McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess"))
        writer.close()


class _RecordingRepository:
    """An async config repository that records what it was asked to do."""

    def __init__(self) -> None:
        self.saved: list[McpServerConfigSnapshot] = []
        self.deleted: list[str] = []

    async def save(self, snapshot: McpServerConfigSnapshot) -> None:
        self.saved.append(snapshot)

    async def delete(self, mcp_server_id: str) -> bool:
        self.deleted.append(mcp_server_id)
        return True


class _NullAudit:
    async def append(self, entry: Any) -> None:
        pass


class TestTheSnapshotHasOneDefinition:
    def test_recovery_and_registration_build_the_same_record(self) -> None:
        # They used to build it separately, one of them reaching into a dozen
        # private attributes. Two copies drift the first time a field is added,
        # and the drift shows up only as a field missing after a restart.
        import inspect

        from mcp_hangar.infrastructure.persistence import recovery_service

        source = inspect.getsource(recovery_service.RecoveryService.save_mcp_server_config)

        assert "snapshot_of(" in source
        assert "McpServerConfigSnapshot(" not in source
