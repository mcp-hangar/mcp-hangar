"""The claim pass marks the rows of the servers the manager's file declares, and nothing else (#2171).

A row the file took over from the API, or one written before a row could be
claimed, is a registration until something claims it, and a restart on a file
that dropped the server then brings it back. The manager claims it: unfenced on
a standalone gateway, under the lease generation when peers share the storage.

The claim is one conditional statement, so what it must not do is decided by
the database: a deposed holder matches nothing, and a row deleted or rewritten
after it was read is left as it is.
"""

from __future__ import annotations

import threading

import pytest

from mcp_hangar.domain.contracts.persistence import ConcurrentModificationError, McpServerConfigSnapshot
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.sqlite_management_lease import SQLiteManagementLease
from mcp_hangar.server.bootstrap import cqrs, file_claims


@pytest.fixture
async def storage(tmp_path):
    """The fleet and the lease in one SQLite file, as the backend lays them out."""
    path = str(tmp_path / "mcp_hangar.db")
    database = Database(DatabaseConfig(path=path))
    await database.initialize()
    return SQLiteMcpServerConfigRepository(database), SQLiteManagementLease(path)


def _snapshot(mcp_server_id: str) -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"])


async def _claimed(configs) -> dict[str, bool]:
    return {row.mcp_server_id: row.claimed_by_file for row in await configs.get_all()}


def _shared_writer(configs, lease: dict) -> RepositoryFleetWriter:
    """A writer on storage peers share, holding whatever tenure *lease* names now."""
    return RepositoryFleetWriter(configs, lease_provider=lambda: lease.get("held"), shared_storage=lambda: True)


@pytest.fixture
def run_pass(monkeypatch):
    """Run `claim_declared_rows` with *writer* as the fleet's and *declared* as the file."""

    def run(writer, declared: set[str], *, manages: bool = True) -> int | None:
        monkeypatch.setattr(cqrs, "_writer", writer)
        monkeypatch.setattr("mcp_hangar.server.config.declared_in_config", lambda sid: sid in declared)
        monkeypatch.setattr(file_claims, "may_manage", lambda: manages)
        return file_claims.claim_declared_rows()

    return run


@pytest.mark.asyncio
class TestTheFence:
    async def test_a_deposed_holder_claims_nothing(self, storage, run_pass) -> None:
        configs, lease_store = storage
        await configs.save(_snapshot("x"))
        stale = lease_store.acquire("gateway-a", ttl_s=0.0)
        lease_store.acquire("gateway-b", ttl_s=3600)

        writer = _shared_writer(configs, {"held": stale})
        assert run_pass(writer, {"x"}) == 0

        assert await _claimed(configs) == {"x": False}

    async def test_the_current_holder_claims(self, storage, run_pass) -> None:
        configs, lease_store = storage
        await configs.save(_snapshot("x"))
        writer = _shared_writer(configs, {"held": lease_store.acquire("gateway-a", ttl_s=3600)})

        assert run_pass(writer, {"x"}) == 1

        assert await _claimed(configs) == {"x": True}

    async def test_holding_nothing_on_shared_storage_claims_nothing(self, storage, run_pass) -> None:
        configs, _ = storage
        await configs.save(_snapshot("x"))

        assert run_pass(_shared_writer(configs, {}), {"x"}) == 0

        assert await _claimed(configs) == {"x": False}

    async def test_a_replica_that_does_not_manage_writes_nothing_and_still_counts(self, storage, run_pass) -> None:
        configs, lease_store = storage
        await configs.save(_snapshot("x"))
        await configs.save(McpServerConfigSnapshot(mcp_server_id="gone", mode="subprocess", claimed_by_file=True))
        writer = _shared_writer(configs, {"held": lease_store.acquire("gateway-a", ttl_s=3600)})

        assert run_pass(writer, {"x"}, manages=False) == 0

        assert await _claimed(configs) == {"x": False, "gone": True}
        assert file_claims.dormant_claimed_rows() == 1

    async def test_a_standalone_gateway_claims_unfenced(self, storage, run_pass) -> None:
        configs, _ = storage
        await configs.save(_snapshot("x"))
        # No keeper, so no lease: the gateway is its own manager.
        writer = RepositoryFleetWriter(configs, lease_provider=lambda: None, shared_storage=lambda: False)

        assert run_pass(writer, {"x"}) == 1

        assert await _claimed(configs) == {"x": True}


@pytest.mark.asyncio
class TestTheRowReadIsTheRowClaimed:
    async def test_a_row_deleted_after_it_was_read_stays_deleted(self, storage) -> None:
        configs, _ = storage
        await configs.save(_snapshot("x"))
        row, version = await configs.get_with_version("x")
        await configs.delete("x")

        claimed = McpServerConfigSnapshot.from_dict({**row.to_dict(), "claimed_by_file": True})
        assert await configs.claim_for_file(claimed, version) is False

        assert await configs.get_all() == []

    async def test_a_row_deleted_and_created_again_after_it_was_read_is_not_claimed(self, storage) -> None:
        configs, _ = storage
        await configs.save(_snapshot("x"))
        row, version = await configs.get_with_version("x")
        await configs.delete("x")
        await configs.save_versioned(_snapshot("x"), None)

        claimed = McpServerConfigSnapshot.from_dict({**row.to_dict(), "claimed_by_file": True})
        assert await configs.claim_for_file(claimed, version) is False

        assert await _claimed(configs) == {"x": False}

    async def test_a_write_read_before_the_claim_is_refused_rather_than_dropping_it(self, storage) -> None:
        configs, _ = storage
        await configs.save(_snapshot("x"))
        row, version = await configs.get_with_version("x")
        RepositoryFleetWriter(configs).claim_for_file("x")

        with pytest.raises(ConcurrentModificationError):
            await configs.save_versioned(row, version)

        assert await _claimed(configs) == {"x": True}


@pytest.mark.asyncio
class TestTheHoldersFileDecides:
    async def test_a_holder_on_file_a_then_file_b_claims_only_what_each_declares(self, storage, run_pass) -> None:
        configs, lease_store = storage
        for mcp_server_id in ("in-a", "in-b", "in-neither"):
            await configs.save(_snapshot(mcp_server_id))
        writer = _shared_writer(configs, {"held": lease_store.acquire("gateway-a", ttl_s=3600)})

        assert run_pass(writer, {"in-a"}) == 1
        assert await _claimed(configs) == {"in-a": True, "in-b": False, "in-neither": False}

        assert run_pass(writer, {"in-b"}) == 1
        # A claim is never taken back: the file that dropped `in-a` skips it.
        assert await _claimed(configs) == {"in-a": True, "in-b": True, "in-neither": False}
        assert file_claims.dormant_claimed_rows() == 1


def test_a_tenure_starting_wakes_the_timer_without_waiting_on_the_database(monkeypatch) -> None:
    passes = threading.Event()
    monkeypatch.setattr(file_claims, "may_manage", lambda: True)
    monkeypatch.setattr(file_claims, "claim_declared_rows", passes.set)
    file_claims._wake.clear()
    worker = file_claims.FileClaimWorker(interval_s=3600)
    worker.start()
    try:
        # The keeper's `on_acquired`, on its own thread: it only sets an event.
        file_claims.request_claim_pass(None)
        assert passes.wait(5), "the timer did not run a pass when a tenure began"
    finally:
        worker.stop()
        assert worker.join(5)
