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

import json
from typing import Any
from unittest.mock import patch

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
from mcp_hangar.domain.security.ssrf import SsrfBlocked, resolve_validated_addresses
from mcp_hangar.domain.services.fleet_snapshot import server_from_snapshot, snapshot_of
from mcp_hangar.domain.value_objects.provenance import Provenance
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

    def delete(self, mcp_server_id: str, *, fenced: bool = False) -> None:
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


def _resolves_to(*addresses: str):
    """Patch name resolution, so the registration-time SSRF check needs no DNS."""
    return patch(
        "mcp_hangar.domain.security.ssrf.socket.getaddrinfo",
        return_value=[(None, None, None, None, (address, 0)) for address in addresses],
    )


def _through_storage(snapshot: McpServerConfigSnapshot) -> McpServerConfigSnapshot:
    """The record as a durable backend holds it: one JSON `config_json` cell.

    Both the SQLite and the PostgreSQL repository serialise `to_dict()` into
    that single column and rebuild with `from_dict`, so a field that survives
    the dataclass but not the serialisation is still gone after the restart
    these tests are about.
    """
    return McpServerConfigSnapshot.from_dict(json.loads(json.dumps(snapshot.to_dict())))


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


@pytest.mark.asyncio
class TestTheConnectTimeGuardSurvivesTheProcess:
    """The SSRF policy is part of the configuration, not of the process.

    `enforce_ssrf` is set at one site -- the registration handler, for a remote
    endpoint it has just SSRF-checked -- and the connect-time re-check is the
    only thing standing between that endpoint and a DNS rebind afterwards. The
    snapshot carries the field; nothing put it there, so every server rebuilt
    from its record came back unguarded, and the guard the release advertises
    lapsed at the first restart with no line in any log.
    """

    async def test_a_remote_registration_comes_back_with_the_guard_on(self) -> None:
        configs = InMemoryMcpServerConfigRepository()
        fleet = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(
            repository=fleet, event_bus=_SilentBus(), fleet_writer=RepositoryFleetWriter(configs)
        )

        with _resolves_to("93.184.216.34"):
            handler.handle(_create("remote", mode="remote", command=None, endpoint="https://mcp.example.com/mcp"))

        assert fleet.get("remote")._enforce_ssrf is True, "registration is what turns the guard on"

        stored = await configs.get("remote")
        assert stored is not None and stored.enforce_ssrf is True

        # A second process, reading what the first one wrote down.
        after_restart_configs = InMemoryMcpServerConfigRepository()
        await after_restart_configs.save(_through_storage(stored))
        after_restart = InMemoryMcpServerRepository()
        recovered = await RecoveryService(
            database=None,
            mcp_server_repository=after_restart,
            config_repository=after_restart_configs,
            audit_repository=_NullAudit(),
        ).recover_mcp_servers()

        assert recovered == ["remote"]
        restored = after_restart.get("remote")
        assert restored._enforce_ssrf is True
        # The seam it has to reach: what the aggregate hands the transport that
        # actually connects, which is where the re-check lives.
        assert restored._get_launch_config()["enforce_ssrf"] is True

    async def test_a_discovered_server_keeps_its_provenance_and_its_addresses(self) -> None:
        # Restoring the flag alone would be the worse bug: a container's own
        # private address, legitimate at registration, refused on every call
        # after the restart because the policy came back as HUMAN.
        configs = InMemoryMcpServerConfigRepository()
        handler = CreateMcpServerHandler(
            repository=InMemoryMcpServerRepository(),
            event_bus=_SilentBus(),
            fleet_writer=RepositoryFleetWriter(configs),
        )

        with _resolves_to("10.88.0.7"):
            handler.handle(
                _create(
                    "container",
                    mode="remote",
                    command=None,
                    endpoint="http://10.88.0.7:8080",
                    source="discovery:docker",
                    provenance=Provenance.DISCOVERY,
                    runtime_addresses=frozenset({"10.88.0.7"}),
                )
            )

        stored = await configs.get("container")
        assert stored is not None
        restored = server_from_snapshot(_through_storage(stored))

        assert restored._provenance is Provenance.DISCOVERY
        assert restored._runtime_addresses == frozenset({"10.88.0.7"})
        with _resolves_to("10.88.0.7"):
            assert resolve_validated_addresses(
                "http://10.88.0.7:8080",
                provenance=restored._provenance,
                runtime_addresses=restored._runtime_addresses,
            ) == ["10.88.0.7"]


class TestTheUpgradeCoversTheRowsAlreadyWrittenDown:
    """The rows 2.5.0 left behind, read by the process that has the fix.

    Writing the flag helps only servers registered afterwards. Everything
    registered while 2.5.0 was running has a row saying `enforce_ssrf: false`,
    and that row is not repaired by an update -- the update snapshots an
    aggregate that was itself rebuilt with the flag off -- so those servers come
    back unguarded for good, not until the next restart. Delete plus
    re-register was the only cure, which is not what an operator reads the
    changelog and does. So the flag is derived from the record.

    The scoping is the part that has to be right: a pre-fix row says nothing
    about provenance, so a discovered server comes back HUMAN with no runtime
    addresses, and deriving the guard over that would refuse a container address
    that works today.
    """

    @staticmethod
    def _row_written_by_2_5_0(**fields: Any) -> McpServerConfigSnapshot:
        """A stored row in the pre-fix shape: the three fields simply absent.

        Built from the dict, not from `snapshot_of`, because that is what is in
        the `config_json` column of a database written by 2.5.0 -- and the
        dataclass defaults (HUMAN / None / False) are exactly what `from_dict`
        supplies for the missing keys.
        """
        row: dict[str, Any] = {"mcp_server_id": "upstream", "mode": "remote", "enabled": True}
        row.update(fields)
        assert not {"provenance", "runtime_addresses", "enforce_ssrf"} & set(row), "that is the post-fix shape"
        return McpServerConfigSnapshot.from_dict(json.loads(json.dumps(row)))

    def test_a_remote_endpoint_registered_under_2_5_0_comes_back_guarded(self) -> None:
        stored = self._row_written_by_2_5_0(endpoint="https://mcp.example.com/mcp")
        assert stored.enforce_ssrf is False, "the row itself cannot say otherwise; that is the whole problem"

        restored = server_from_snapshot(stored)

        assert restored._enforce_ssrf is True
        # The seam that decides whether the transport re-checks at all.
        assert restored._get_launch_config()["enforce_ssrf"] is True

    def test_a_row_saying_false_out_loud_is_the_same_row(self) -> None:
        # A row written by 2.5.0 through `to_dict` carries the key with the
        # default in it, so "absent" and "false" are the same population and a
        # derivation that only handled the absent key would cover neither.
        stored = McpServerConfigSnapshot.from_dict(
            json.loads(
                json.dumps(
                    {
                        "mcp_server_id": "upstream",
                        "mode": "remote",
                        "endpoint": "https://mcp.example.com/mcp",
                        "enforce_ssrf": False,
                        "provenance": "human",
                        "runtime_addresses": None,
                    }
                )
            )
        )
        assert server_from_snapshot(stored)._enforce_ssrf is True

    def test_a_discovered_container_address_is_not_newly_refused(self) -> None:
        # The outage this must not cause. Both container sources build the
        # endpoint out of the address the runtime reported -- `http://{pod_ip}:
        # {port}` in Kubernetes, `http://{host}:{port}` in Docker -- so a
        # discovered row holds a literal, and a pre-fix one has lost the
        # provenance that made that literal legitimate.
        stored = self._row_written_by_2_5_0(endpoint="http://10.88.0.7:8080")
        restored = server_from_snapshot(stored)

        assert restored._enforce_ssrf is False
        assert restored._get_launch_config()["enforce_ssrf"] is False

        # And this is why leaving it off is the only safe answer: with the
        # provenance gone, the guard would refuse the address on every call.
        with _resolves_to("10.88.0.7"), pytest.raises(SsrfBlocked):
            resolve_validated_addresses(
                "http://10.88.0.7:8080",
                provenance=restored._provenance,
                runtime_addresses=restored._runtime_addresses,
            )

    def test_a_pod_address_outside_the_refused_ranges_is_guarded_and_still_reachable(self) -> None:
        # 100.64.0.0/10 is a common Kubernetes pod and service range and is
        # deliberately not in the strict policy's refused list. Deriving the
        # guard for it is therefore safe -- the question the derivation asks is
        # "would the guard refuse this address", not "does it look private".
        restored = server_from_snapshot(self._row_written_by_2_5_0(endpoint="http://100.64.3.9:8080"))

        assert restored._enforce_ssrf is True
        with _resolves_to("100.64.3.9"):
            assert resolve_validated_addresses(
                "http://100.64.3.9:8080",
                provenance=restored._provenance,
                runtime_addresses=restored._runtime_addresses,
            ) == ["100.64.3.9"]

    def test_a_loopback_endpoint_a_container_published_is_left_alone(self) -> None:
        # Docker discovery prefers a container's published host binding, and
        # rewrites a wildcard bind to 127.0.0.1 -- the endpoint an operator
        # running the quickstart actually has in the store.
        assert (
            server_from_snapshot(self._row_written_by_2_5_0(endpoint="http://127.0.0.1:18080"))._enforce_ssrf is False
        )

    def test_nothing_that_was_never_registration_checked_is_guarded(self) -> None:
        # The derivation may only cover what `validate_no_ssrf` covered, which
        # is a remote mode with an endpoint and nothing else.
        for row in (
            self._row_written_by_2_5_0(mode="subprocess", command=["python", "-m", "server"]),
            self._row_written_by_2_5_0(endpoint=None),
            self._row_written_by_2_5_0(mode="docker", image="ghcr.io/example/server:1"),
        ):
            assert server_from_snapshot(row)._enforce_ssrf is False, row.mode

    def test_a_row_written_after_the_fix_is_read_as_written(self) -> None:
        # The derivation only ever adds. A discovered server recorded by the
        # fixed code keeps the guard *and* the scoping that makes its private
        # address legitimate, rather than being flattened back to the pre-fix
        # answer for holding a literal.
        stored = _through_storage(
            McpServerConfigSnapshot(
                mcp_server_id="upstream",
                mode="remote",
                endpoint="http://10.88.0.7:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.88.0.7"}),
                enforce_ssrf=True,
            )
        )
        restored = server_from_snapshot(stored)

        assert restored._enforce_ssrf is True
        assert restored._provenance is Provenance.DISCOVERY
        assert restored._runtime_addresses == frozenset({"10.88.0.7"})

    def test_the_derived_guard_is_written_back_by_the_next_update(self) -> None:
        # The row heals itself: once the server is in memory with the guard on,
        # the next thing that records its configuration records the flag too,
        # so the derivation is needed once rather than on every start.
        restored = server_from_snapshot(self._row_written_by_2_5_0(endpoint="https://mcp.example.com/mcp"))
        restored.update_config(description="renamed by an operator")

        assert snapshot_of(restored).enforce_ssrf is True


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
