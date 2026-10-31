"""What the group API does is written down, and read back over what the file built (#2172).

The full path runs in ``tests/integration/test_a_restart_keeps_groups_made_over_rest.py``.
This file pins the parts that path does not reach: each repository's rows, the
order of a handler's write and its change, and the refusals of a restore.
No local PostgreSQL: its SQL is read off a mocked connection, as in
``test_postgres_config_repository.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    DeleteGroupCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    AddGroupMemberHandler,
    CreateGroupHandler,
    DeleteGroupHandler,
    RemoveGroupMemberHandler,
    UpdateGroupHandler,
)
from mcp_hangar.domain.contracts.fleet import IGroupWriter
from mcp_hangar.domain.contracts.persistence import GroupMemberRecord, GroupRecord, PersistenceError, StoredGroups
from mcp_hangar.domain.exceptions import ConfigurationUnavailableError, ValidationError
from mcp_hangar.domain.model import GroupCreated, GroupMemberAdded, LoadBalancerStrategy, McpServer, McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.peer_identity import PeerIdentity
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.backends.postgresql.config_repository import (
    PostgresMcpServerConfigRepository,
)
from mcp_hangar.infrastructure.persistence.config_repository import (
    InMemoryMcpServerConfigRepository,
    SQLiteMcpServerConfigRepository,
)
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.server import config as server_config
from mcp_hangar.server.bootstrap import persistence as bootstrap_persistence
from mcp_hangar.server.state import GROUPS


def _server(mcp_server_id: str) -> McpServer:
    return McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["python", "-m", "server"])


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_local(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
        return 0


class _RefusingWriter(IGroupWriter):
    def create_group(self, record: GroupRecord) -> bool:
        raise PersistenceError("the database is unreachable")

    def update_group(
        self, group_id: str, strategy: str | None, min_healthy: int | None, description: str | None
    ) -> None:
        raise PersistenceError("the database is unreachable")

    def save_group_member(self, record: GroupMemberRecord) -> None:
        raise PersistenceError("the database is unreachable")

    def delete_group(self, group_id: str) -> None:
        raise PersistenceError("the database is unreachable")

    def delete_group_member(self, group_id: str, member_id: str) -> None:
        raise PersistenceError("the database is unreachable")


class TestSQLiteRows:
    @pytest.fixture
    def repository(self, tmp_path) -> SQLiteMcpServerConfigRepository:
        return SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))

    def test_a_group_and_a_member_read_back(self, repository) -> None:
        async def scenario() -> StoredGroups:
            assert await repository.create_group(GroupRecord("runtime", "priority", 2, "from REST"))
            await repository.save_group_member(GroupMemberRecord("pool", "kept", 3, 2))
            return await repository.read_groups()

        assert asyncio.run(scenario()) == StoredGroups(
            (GroupRecord("runtime", "priority", 2, "from REST"),), (GroupMemberRecord("pool", "kept", 3, 2),)
        )

    def test_a_second_create_of_one_id_changes_nothing(self, repository) -> None:
        async def scenario() -> tuple[bool, StoredGroups]:
            await repository.create_group(GroupRecord("runtime", "priority"))
            return await repository.create_group(GroupRecord("runtime", "random")), await repository.read_groups()

        created, stored = asyncio.run(scenario())
        assert created is False
        assert stored.groups[0].strategy == "priority"

    def test_a_group_created_again_does_not_inherit_old_member_rows(self, repository) -> None:
        async def scenario() -> StoredGroups:
            await repository.save_group_member(GroupMemberRecord("pool", "from-before"))
            await repository.create_group(GroupRecord("pool", "priority"))
            return await repository.read_groups()

        assert asyncio.run(scenario()).members == ()

    def test_a_refused_create_leaves_the_groups_members(self, repository) -> None:
        async def scenario() -> StoredGroups:
            await repository.create_group(GroupRecord("pool", "priority"))
            await repository.save_group_member(GroupMemberRecord("pool", "one"))
            await repository.create_group(GroupRecord("pool", "random"))
            return await repository.read_groups()

        assert asyncio.run(scenario()).members == (GroupMemberRecord("pool", "one"),)

    def test_an_update_changes_only_the_fields_it_names(self, repository) -> None:
        async def scenario() -> StoredGroups:
            await repository.create_group(GroupRecord("runtime", "priority", 2, "from REST"))
            await repository.update_group("runtime", "random", None, None)
            return await repository.read_groups()

        assert asyncio.run(scenario()).groups == (GroupRecord("runtime", "random", 2, "from REST"),)

    def test_an_update_of_a_group_with_no_row_writes_none(self, repository) -> None:
        async def scenario() -> StoredGroups:
            await repository.update_group("declared-by-the-file", "random", None, None)
            return await repository.read_groups()

        assert asyncio.run(scenario()) == StoredGroups()

    def test_saving_a_member_again_replaces_it(self, repository) -> None:
        async def scenario() -> StoredGroups:
            await repository.save_group_member(GroupMemberRecord("pool", "kept", 3, 2))
            await repository.save_group_member(GroupMemberRecord("pool", "kept", 4, 1))
            return await repository.read_groups()

        assert asyncio.run(scenario()).members == (GroupMemberRecord("pool", "kept", 4, 1),)

    def test_a_delete_takes_the_group_and_its_members_and_no_other(self, repository) -> None:
        async def scenario() -> StoredGroups:
            await repository.create_group(GroupRecord("runtime", "priority"))
            await repository.save_group_member(GroupMemberRecord("runtime", "one"))
            await repository.save_group_member(GroupMemberRecord("pool", "one"))
            await repository.save_group_member(GroupMemberRecord("pool", "two"))
            await repository.delete_group("runtime")
            await repository.delete_group_member("pool", "one")
            return await repository.read_groups()

        assert asyncio.run(scenario()) == StoredGroups((), (GroupMemberRecord("pool", "two"),))


class TestPostgresRows:
    @pytest.fixture
    def fixture(self) -> tuple[PostgresMcpServerConfigRepository, MagicMock, MagicMock]:
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
        connection.cursor.return_value.__exit__ = Mock(return_value=False)

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield connection

        repository = PostgresMcpServerConfigRepository(connection_factory=_Factory())
        self.schema = cursor.execute.call_args_list[0].args[0]
        cursor.reset_mock()
        connection.reset_mock()
        return repository, connection, cursor

    def test_the_schema_has_both_tables(self, fixture) -> None:
        assert "CREATE TABLE IF NOT EXISTS mcp_server_groups" in self.schema
        assert "CREATE TABLE IF NOT EXISTS mcp_server_group_members" in self.schema

    def test_a_second_create_of_one_id_answers_false(self, fixture) -> None:
        repository, connection, cursor = fixture
        cursor.rowcount = 0
        assert asyncio.run(repository.create_group(GroupRecord("runtime", "priority"))) is False
        stale, insert = cursor.execute.call_args_list
        assert "NOT EXISTS" in stale.args[0]
        sql, parameters = insert.args
        assert "ON CONFLICT (group_id) DO NOTHING" in sql
        assert parameters == ("runtime", "priority", 1, None)
        connection.commit.assert_called_once()

    def test_saving_a_member_replaces_its_weight_and_priority(self, fixture) -> None:
        repository, _, cursor = fixture
        asyncio.run(repository.save_group_member(GroupMemberRecord("pool", "kept", 3, 2)))
        sql, parameters = cursor.execute.call_args.args
        assert "DO UPDATE SET weight = excluded.weight, priority = excluded.priority" in sql
        assert parameters == ("pool", "kept", 3, 2)

    def test_a_delete_is_one_transaction_over_both_tables(self, fixture) -> None:
        repository, connection, cursor = fixture
        asyncio.run(repository.delete_group("runtime"))
        tables = [call.args[0].split()[2] for call in cursor.execute.call_args_list]
        assert tables == ["mcp_server_groups", "mcp_server_group_members"]
        connection.commit.assert_called_once()

    def test_groups_are_read_in_one_transaction(self, fixture) -> None:
        repository, _, cursor = fixture
        cursor.fetchall.side_effect = [[("runtime", "priority", 2, "d")], [("pool", "kept", 3, 2)]]
        assert asyncio.run(repository.read_groups()) == StoredGroups(
            (GroupRecord("runtime", "priority", 2, "d"),), (GroupMemberRecord("pool", "kept", 3, 2),)
        )

    def test_a_failed_write_rolls_back_and_raises(self, fixture) -> None:
        repository, connection, cursor = fixture
        cursor.execute.side_effect = RuntimeError("lost")
        with pytest.raises(PersistenceError):
            asyncio.run(repository.delete_group_member("pool", "kept"))
        connection.rollback.assert_called_once()


class TestAWriteComesFirst:
    """A change whose durable half failed answers with the failure and changes nothing."""

    def test_a_group_is_not_created(self) -> None:
        groups: dict[str, McpServerGroup] = {}
        with pytest.raises(PersistenceError):
            CreateGroupHandler(groups, _Bus(), _RefusingWriter()).handle(CreateGroupCommand(group_id="runtime"))
        assert groups == {}

    def test_a_row_another_replica_wrote_refuses_the_create(self) -> None:
        writer = MagicMock(spec=IGroupWriter)
        writer.create_group.return_value = False
        with pytest.raises(ValidationError):
            CreateGroupHandler({}, _Bus(), writer).handle(CreateGroupCommand(group_id="runtime"))

    def test_a_group_is_not_updated(self) -> None:
        groups = {"runtime": McpServerGroup("runtime")}
        command = UpdateGroupCommand(group_id="runtime", strategy="random")
        with pytest.raises(PersistenceError):
            UpdateGroupHandler(groups, _Bus(), _RefusingWriter()).handle(command)
        assert groups["runtime"].strategy is LoadBalancerStrategy.ROUND_ROBIN

    def test_a_group_is_not_deleted(self) -> None:
        groups = {"runtime": McpServerGroup("runtime")}
        with pytest.raises(PersistenceError):
            DeleteGroupHandler(groups, _Bus(), _RefusingWriter()).handle(DeleteGroupCommand(group_id="runtime"))
        assert "runtime" in groups

    def _add(self, writer: Any, group: McpServerGroup, **fields: Any) -> None:
        repository = InMemoryMcpServerRepository()
        repository.add("one", _server("one"))
        command = AddGroupMemberCommand(group_id=str(group.id), mcp_server_id="one", **fields)
        AddGroupMemberHandler(repository, {str(group.id): group}, _Bus(), writer).handle(command)

    def test_a_member_does_not_join(self) -> None:
        group = McpServerGroup("runtime", auto_start=False)
        with pytest.raises(PersistenceError):
            self._add(_RefusingWriter(), group)
        assert group.members == []

    def test_a_member_is_recorded_with_its_weight_and_priority(self) -> None:
        writer = MagicMock(spec=IGroupWriter)
        self._add(writer, McpServerGroup("runtime", auto_start=False), weight=3, priority=2)
        writer.save_group_member.assert_called_once_with(GroupMemberRecord("runtime", "one", 3, 2))

    def test_a_member_already_there_gets_no_row(self) -> None:
        group = McpServerGroup("runtime", auto_start=False)
        group.add_member(_server("one"))
        writer = MagicMock(spec=IGroupWriter)
        with pytest.raises(ValueError):
            self._add(writer, group)
        writer.save_group_member.assert_not_called()

    def test_a_weight_out_of_range_writes_no_row(self) -> None:
        writer = MagicMock(spec=IGroupWriter)
        with pytest.raises(ValueError):
            self._add(writer, McpServerGroup("runtime", auto_start=False), weight=0)
        writer.save_group_member.assert_not_called()

    def test_a_member_is_not_removed(self) -> None:
        group = McpServerGroup("runtime", auto_start=False)
        group.add_member(_server("one"))
        command = RemoveGroupMemberCommand(group_id="runtime", mcp_server_id="one")
        with pytest.raises(PersistenceError):
            RemoveGroupMemberHandler({"runtime": group}, _Bus(), _RefusingWriter()).handle(command)
        assert group.get_member("one") is not None


def test_the_fleet_writer_waits_for_each_group_write(tmp_path) -> None:
    repository = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    writer = RepositoryFleetWriter(repository)
    try:
        assert writer.create_group(GroupRecord("runtime", "priority"))
        writer.update_group("runtime", "random", None, None)
        writer.save_group_member(GroupMemberRecord("runtime", "one"))
        writer.save_group_member(GroupMemberRecord("runtime", "two"))
        writer.delete_group_member("runtime", "one")
        assert asyncio.run(repository.read_groups()) == StoredGroups(
            (GroupRecord("runtime", "random"),), (GroupMemberRecord("runtime", "two"),)
        )
        writer.delete_group("runtime")
        assert asyncio.run(repository.read_groups()) == StoredGroups()
    finally:
        writer.close()


class TestRestore:
    @pytest.fixture(autouse=True)
    def clean(self):
        GROUPS.clear()
        server_config._DECLARED_GROUPS.clear()
        yield
        GROUPS.clear()
        server_config._DECLARED_GROUPS.clear()

    def test_a_group_comes_back_with_no_event_and_not_as_the_files(self) -> None:
        repository = InMemoryMcpServerRepository()
        repository.add("one", _server("one"))
        stored = StoredGroups(
            (GroupRecord("runtime", "priority", 2, "d"),), (GroupMemberRecord("runtime", "one", 3, 2),)
        )
        server_config.restore_stored_groups(stored, repository)
        group = GROUPS["runtime"]
        member = group.get_member("one")
        assert (group.strategy, group.description) == (LoadBalancerStrategy.PRIORITY, "d")
        assert (member.weight, member.priority, member.in_rotation) == (3, 2, True)
        assert member.mcp_server is repository.get("one")
        assert not [e for e in group.collect_events() if isinstance(e, (GroupCreated, GroupMemberAdded))]
        assert server_config._DECLARED_GROUPS == {}

    def test_a_member_of_a_group_that_does_not_start_members_waits_outside_rotation(self) -> None:
        repository = InMemoryMcpServerRepository()
        repository.add("one", _server("one"))
        GROUPS["pool"] = McpServerGroup("pool", auto_start=False)
        server_config.restore_stored_groups(StoredGroups((), (GroupMemberRecord("pool", "one"),)), repository)
        assert GROUPS["pool"].get_member("one").in_rotation is False

    def test_the_file_keeps_a_group_and_a_member_it_declares(self) -> None:
        repository = InMemoryMcpServerRepository()
        repository.add("one", _server("one"))
        declared = McpServerGroup("pool", LoadBalancerStrategy.RANDOM, auto_start=False)
        declared.add_member(repository.get("one"), weight=7)
        GROUPS["pool"] = declared
        stored = StoredGroups((GroupRecord("pool", "priority"),), (GroupMemberRecord("pool", "one", 3, 2),))
        server_config.restore_stored_groups(stored, repository)
        assert GROUPS["pool"] is declared
        assert declared.strategy is LoadBalancerStrategy.RANDOM
        assert declared.get_member("one").weight == 7

    def test_a_member_row_under_no_group_is_not_applied(self) -> None:
        repository = InMemoryMcpServerRepository()
        repository.add("one", _server("one"))
        server_config.restore_stored_groups(StoredGroups((), (GroupMemberRecord("dropped", "one"),)), repository)
        assert GROUPS == {}

    def test_a_key_pinned_server_does_not_come_back_as_a_member(self) -> None:
        repository = InMemoryMcpServerRepository()
        pinned = _server("pinned")
        pinned._peer_identity = MagicMock(spec=PeerIdentity)
        repository.add("pinned", pinned)
        stored = StoredGroups((GroupRecord("runtime", "priority"),), (GroupMemberRecord("runtime", "pinned"),))
        server_config.restore_stored_groups(stored, repository)
        assert GROUPS["runtime"].members == []

    def test_a_member_whose_server_is_gone_is_not_applied(self) -> None:
        stored = StoredGroups((GroupRecord("runtime", "priority"),), (GroupMemberRecord("runtime", "gone"),))
        server_config.restore_stored_groups(stored, InMemoryMcpServerRepository())
        assert GROUPS["runtime"].members == []

    def test_a_strategy_nothing_reads_keeps_the_group(self) -> None:
        stored = StoredGroups((GroupRecord("runtime", "retired-strategy"),), ())
        server_config.restore_stored_groups(stored, InMemoryMcpServerRepository())
        assert GROUPS["runtime"].strategy is LoadBalancerStrategy.ROUND_ROBIN


class TestReadingTheRowsAtBoot:
    def _runtime(self, repository: Any, *, auto_recover: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            config_repository=repository,
            recovery_service=object(),
            persistence_config=SimpleNamespace(enabled=True, auto_recover=auto_recover),
        )

    def _use(self, monkeypatch, runtime: Any) -> None:
        monkeypatch.setattr(server_config, "get_runtime", lambda: runtime)

    def test_a_store_that_cannot_be_read_stops_the_boot(self, monkeypatch) -> None:
        repository = MagicMock()

        async def refuse() -> StoredGroups:
            raise PersistenceError("the database is unreachable")

        repository.read_groups = refuse
        runtime = self._runtime(repository)
        self._use(monkeypatch, runtime)
        with pytest.raises(ConfigurationUnavailableError):
            bootstrap_persistence.read_stored_groups(runtime)

    def test_the_in_memory_backend_has_none(self, monkeypatch) -> None:
        runtime = self._runtime(InMemoryMcpServerConfigRepository())
        self._use(monkeypatch, runtime)
        assert bootstrap_persistence.read_stored_groups(runtime) is None

    def test_with_recovery_off_they_stay_unread_as_the_servers_do(self, monkeypatch) -> None:
        repository = MagicMock()
        runtime = self._runtime(repository, auto_recover=False)
        self._use(monkeypatch, runtime)
        assert bootstrap_persistence.read_stored_groups(runtime) is None
        repository.read_groups.assert_not_called()
