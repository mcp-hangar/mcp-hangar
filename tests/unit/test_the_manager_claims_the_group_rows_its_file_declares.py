"""The claim pass marks the group and member rows the manager's file declares, and a restart skips them (#2171).

A group the file took over from the group API, or a member the file named after
the API added it, keeps its row. Until something claims that row, a restart on
a file that dropped the group or the member restores it where a reload removes
it. The manager claims it, fenced as a server's row is; the restore skips a
claimed row the file in force does not declare, and leaves it in place for a
peer whose file still does.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_hangar.domain.contracts.persistence import GroupMemberRecord, GroupRecord, StoredGroups
from mcp_hangar.domain.model import McpServer, McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.sqlite_management_lease import SQLiteManagementLease
from mcp_hangar.server import config as server_config
from mcp_hangar.server.bootstrap import cqrs, file_claims
from mcp_hangar.server.state import GROUPS


@pytest.fixture
def storage(tmp_path):
    """The group rows and the lease in one SQLite file, as the backend lays them out."""
    path = str(tmp_path / "mcp_hangar.db")
    database = Database(DatabaseConfig(path=path))
    asyncio.run(database.initialize())
    return SQLiteMcpServerConfigRepository(database), SQLiteManagementLease(path)


@pytest.fixture(autouse=True)
def file_declares(monkeypatch):
    """Set what the file in force declares: group id to the members it names."""
    GROUPS.clear()
    server_config._DECLARED_GROUPS.clear()

    def declare(groups: dict[str, set[str]]) -> None:
        server_config._DECLARED_GROUPS.clear()
        server_config._DECLARED_GROUPS.update({gid: frozenset(members) for gid, members in groups.items()})

    yield declare
    GROUPS.clear()
    server_config._DECLARED_GROUPS.clear()


def _seed(configs) -> None:
    async def seed() -> None:
        await configs.create_group(GroupRecord("runtime", "priority"))
        await configs.create_group(GroupRecord("rest-only", "priority"))
        await configs.save_group_member(GroupMemberRecord("pool", "kept", 3, 2))
        await configs.save_group_member(GroupMemberRecord("pool", "rest-member"))
        await configs.save_group_member(GroupMemberRecord("runtime", "runtime-member"))

    asyncio.run(seed())


def _claims(configs) -> dict[str, bool]:
    stored = asyncio.run(configs.read_groups())
    claims = {g.group_id: g.claimed_by_file for g in stored.groups}
    claims.update({f"{m.group_id}/{m.member_id}": m.claimed_by_file for m in stored.members})
    return claims


def _pass(monkeypatch, writer, *, manages: bool = True) -> int | None:
    monkeypatch.setattr(cqrs, "_writer", writer)
    monkeypatch.setattr(file_claims, "may_manage", lambda: manages)
    return file_claims.claim_declared_rows()


UNCLAIMED = dict.fromkeys(("rest-only", "runtime", "pool/kept", "pool/rest-member", "runtime/runtime-member"), False)


class TestThePass:
    def test_a_standalone_gateway_claims_what_its_file_declares_and_nothing_else(
        self, storage, monkeypatch, file_declares
    ) -> None:
        configs, _ = storage
        _seed(configs)
        file_declares({"runtime": set(), "pool": {"kept"}})
        writer = RepositoryFleetWriter(configs, lease_provider=lambda: None, shared_storage=lambda: False)

        assert _pass(monkeypatch, writer) == 2

        assert _claims(configs) == {**UNCLAIMED, "runtime": True, "pool/kept": True}

    def test_the_current_holder_claims(self, storage, monkeypatch, file_declares) -> None:
        configs, lease_store = storage
        _seed(configs)
        file_declares({"runtime": set()})
        held = lease_store.acquire("gateway-a", ttl_s=3600)
        writer = RepositoryFleetWriter(configs, lease_provider=lambda: held, shared_storage=lambda: True)

        assert _pass(monkeypatch, writer) == 1

        assert _claims(configs)["runtime"] is True

    def test_a_deposed_holder_claims_nothing(self, storage, monkeypatch, file_declares) -> None:
        configs, lease_store = storage
        _seed(configs)
        file_declares({"runtime": set(), "pool": {"kept"}})
        stale = lease_store.acquire("gateway-a", ttl_s=0.0)
        lease_store.acquire("gateway-b", ttl_s=3600)
        writer = RepositoryFleetWriter(configs, lease_provider=lambda: stale, shared_storage=lambda: True)

        assert _pass(monkeypatch, writer) == 0

        assert _claims(configs) == UNCLAIMED

    def test_holding_nothing_on_shared_storage_claims_nothing(self, storage, monkeypatch, file_declares) -> None:
        configs, _ = storage
        _seed(configs)
        file_declares({"runtime": set()})
        writer = RepositoryFleetWriter(configs, lease_provider=lambda: None, shared_storage=lambda: True)

        assert _pass(monkeypatch, writer) == 0

        assert _claims(configs) == UNCLAIMED

    def test_a_replica_that_does_not_manage_writes_nothing_and_counts_the_dormant_rows(
        self, storage, monkeypatch, file_declares
    ) -> None:
        configs, _ = storage
        _seed(configs)
        writer = RepositoryFleetWriter(configs)
        file_declares({"runtime": set(), "pool": {"kept"}})
        _pass(monkeypatch, writer)
        file_declares({"pool": set()})

        assert _pass(monkeypatch, writer, manages=False) == 0

        assert file_claims.dormant_claimed_rows() == 2


class TestTheRowClaimed:
    def test_a_row_deleted_after_it_was_read_stays_deleted(self, storage) -> None:
        configs, _ = storage
        _seed(configs)
        asyncio.run(configs.delete_group("runtime"))

        assert asyncio.run(configs.claim_group_row_for_file("runtime")) is False
        assert asyncio.run(configs.claim_group_row_for_file("runtime", "runtime-member")) is False

        assert "runtime" not in _claims(configs)

    def test_a_claim_is_made_once(self, storage) -> None:
        configs, _ = storage
        _seed(configs)

        assert asyncio.run(configs.claim_group_row_for_file("pool", "kept")) is True
        assert asyncio.run(configs.claim_group_row_for_file("pool", "kept")) is False

    def test_a_member_added_again_over_rest_is_no_longer_claimed(self, storage) -> None:
        configs, _ = storage
        _seed(configs)
        asyncio.run(configs.claim_group_row_for_file("pool", "kept"))

        asyncio.run(configs.save_group_member(GroupMemberRecord("pool", "kept", 5, 1)))

        assert asyncio.run(configs.read_groups()).members[0] == GroupMemberRecord("pool", "kept", 5, 1)

    def test_a_group_created_again_over_a_claimed_row_replaces_it_and_keeps_its_members(self, storage) -> None:
        configs, _ = storage
        _seed(configs)
        asyncio.run(configs.claim_group_row_for_file("runtime"))

        assert asyncio.run(configs.create_group(GroupRecord("runtime", "random", 2))) is True
        assert asyncio.run(configs.create_group(GroupRecord("runtime", "priority"))) is False

        stored = asyncio.run(configs.read_groups())
        assert GroupRecord("runtime", "random", 2) in stored.groups
        assert GroupMemberRecord("runtime", "runtime-member") in stored.members


def _server(mcp_server_id: str) -> McpServer:
    return McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["python", "-m", "server"])


class TestTheRestore:
    @pytest.fixture
    def fleet(self) -> InMemoryMcpServerRepository:
        repository = InMemoryMcpServerRepository()
        for mcp_server_id in ("kept", "rest-member", "runtime-member"):
            repository.add(mcp_server_id, _server(mcp_server_id))
        return repository

    STORED = StoredGroups(
        (GroupRecord("runtime", "priority", claimed_by_file=True),),
        (
            GroupMemberRecord("pool", "kept", 3, 2, claimed_by_file=True),
            GroupMemberRecord("pool", "rest-member"),
            GroupMemberRecord("runtime", "runtime-member"),
        ),
    )

    def test_a_claimed_group_and_member_the_file_dropped_are_skipped(self, fleet, file_declares) -> None:
        file_declares({"pool": set()})
        GROUPS["pool"] = McpServerGroup("pool", auto_start=False)

        server_config.restore_stored_groups(self.STORED, fleet)

        assert "runtime" not in GROUPS
        assert [str(member.id) for member in GROUPS["pool"].members] == ["rest-member"]

    def test_a_claimed_group_the_file_still_declares_takes_its_rest_members(self, fleet, file_declares) -> None:
        file_declares({"runtime": set(), "pool": {"kept"}})
        GROUPS["runtime"] = McpServerGroup("runtime", auto_start=False)
        declared = McpServerGroup("pool", auto_start=False)
        declared.add_member(fleet.get("kept"), weight=7)
        GROUPS["pool"] = declared

        server_config.restore_stored_groups(self.STORED, fleet)

        assert [str(member.id) for member in GROUPS["runtime"].members] == ["runtime-member"]
        assert declared.get_member("kept").weight == 7
        assert declared.get_member("rest-member") is not None
