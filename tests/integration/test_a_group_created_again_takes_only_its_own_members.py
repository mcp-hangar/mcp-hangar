"""A group created again over a row a file claimed takes only the members added after it (#2231).

A file declared group `pool`, a member was added to it over REST, and the file
dropped the group: its row is claimed and dormant. `POST /api/groups` then
creates `pool` again, which replaces that row (#2228) and leaves the member rows
for a peer whose file still declares the group. A restart of this replica used
to attach every one of them to the new group, so it routed to a server its
creator never added. Each creation is now a generation of the row, and a member
row records the generation it was added to:

- a replica that restores `pool` from its row takes only the members of the
  row's generation;
- a peer whose file declares `pool` takes them all, as before.

Run on SQLite always, and on PostgreSQL when ``HANGAR_TEST_POSTGRES_DSN`` names
one (the `live` CI job has it), against the real repositories and the real
restore, as a restart runs it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from mcp_hangar.domain.contracts.persistence import GroupMemberRecord, GroupRecord
from mcp_hangar.domain.model import McpServer, McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.server import config as server_config
from mcp_hangar.server.state import GROUPS

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")
_TABLES = "mcp_server_configs, mcp_server_groups, mcp_server_group_members"


def _sqlite(tmp_path: Any) -> Iterator[Any]:
    database = Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))
    asyncio.run(database.initialize())
    yield SQLiteMcpServerConfigRepository(database)


def _postgres(_tmp_path: Any) -> Iterator[Any]:
    psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")
    from mcp_hangar.infrastructure.persistence.backends.postgresql.config_repository import (
        PostgresMcpServerConfigRepository,
    )

    class _DirectFactory:
        @contextmanager
        def get_connection(self) -> Iterator[Any]:
            conn = psycopg2.connect(DSN)
            try:
                yield conn
            finally:
                conn.close()

    factory = _DirectFactory()

    def drop() -> None:
        with factory.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_TABLES}")
            conn.commit()

    drop()
    yield PostgresMcpServerConfigRepository(factory)
    drop()


@pytest.fixture(
    params=[
        pytest.param(_sqlite, id="sqlite"),
        pytest.param(
            _postgres,
            id="postgresql",
            marks=[
                pytest.mark.live,
                pytest.mark.skipif(not DSN, reason="HANGAR_TEST_POSTGRES_DSN is not set"),
            ],
        ),
    ]
)
def configs(request: pytest.FixtureRequest, tmp_path: Any) -> Iterator[Any]:
    yield from request.param(tmp_path)


@pytest.fixture(autouse=True)
def fresh_groups() -> Iterator[None]:
    GROUPS.clear()
    server_config._DECLARED_GROUPS.clear()
    yield
    GROUPS.clear()
    server_config._DECLARED_GROUPS.clear()


@pytest.fixture
def fleet() -> InMemoryMcpServerRepository:
    repository = InMemoryMcpServerRepository()
    for mcp_server_id in ("from-before", "added-after"):
        repository.add(mcp_server_id, McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"]))
    return repository


def _create_pool_again(configs: Any) -> None:
    """The group's history up to and after the create over its claimed row."""

    async def history() -> None:
        assert await configs.create_group(GroupRecord("pool", "priority"))
        await configs.save_group_member(GroupMemberRecord("pool", "from-before"))
        # The file took the group over and then dropped it: the row is claimed and dormant.
        assert await configs.claim_group_row_for_file("pool")
        assert await configs.create_group(GroupRecord("pool", "random"))
        await configs.save_group_member(GroupMemberRecord("pool", "added-after"))

    asyncio.run(history())


def _members(group_id: str) -> list[str]:
    return sorted(str(member.id) for member in GROUPS[group_id].members)


def test_a_restart_restores_the_group_created_again_with_only_its_own_members(configs: Any, fleet: Any) -> None:
    _create_pool_again(configs)

    server_config.restore_stored_groups(asyncio.run(configs.read_groups()), fleet)

    assert _members("pool") == ["added-after"]


def test_a_peer_whose_file_declares_the_group_still_takes_the_earlier_members(configs: Any, fleet: Any) -> None:
    _create_pool_again(configs)
    server_config._DECLARED_GROUPS["pool"] = frozenset()
    GROUPS["pool"] = McpServerGroup("pool", auto_start=False)

    server_config.restore_stored_groups(asyncio.run(configs.read_groups()), fleet)

    assert _members("pool") == ["added-after", "from-before"]


def test_a_group_never_created_again_keeps_every_member(configs: Any, fleet: Any) -> None:
    """Rows written before generations existed, and every group created once, read generation 0."""

    async def history() -> None:
        assert await configs.create_group(GroupRecord("pool", "priority"))
        await configs.save_group_member(GroupMemberRecord("pool", "from-before"))
        await configs.save_group_member(GroupMemberRecord("pool", "added-after"))

    asyncio.run(history())

    server_config.restore_stored_groups(asyncio.run(configs.read_groups()), fleet)

    assert _members("pool") == ["added-after", "from-before"]
