"""A server a reload declares again starts with the L7 policy its stored row holds (#2332).

An L7 push to a server the file declares writes the server's row, claimed by
the file (#2171). When the file drops the server the row stays, dormant, with
the policy on it, and a restart on a file that declares the server again reads
that policy back (#1306). A reload did not: it built the server from the file
alone, and carried state only from a running object under the same id, which a
server the reload adds does not have. So a reload and a restart of the same
file disagreed, and the server ran without the policy until the next push.

Through the real `ReloadConfigurationHandler` and `ServerConfigLoader`, with
the push made by the real `SetL7PolicyHandler` writing the row. On SQLite
always, and on PostgreSQL when ``HANGAR_TEST_POSTGRES_DSN`` names one (the
`live` CI job has it). The black-box run over streamable HTTP is in
`tests/live`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.crud_commands import SetL7PolicyCommand
from mcp_hangar.application.commands.crud_handlers import SetL7PolicyHandler
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.exceptions import ConfigurationUnavailableError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolAction, ToolRules, evaluate_tool
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.log_buffer import remove_log_buffer
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import ServerConfigLoader, load_configuration
from mcp_hangar.server.state import GROUPS, get_runtime

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")
SERVER = "declared-again"
OTHER = "kept"
#: Denies `echo`, the tool a call outside the policy names.
POLICY = L7Policy(tools=ToolRules(allow=("ping",)), default_action=ToolAction.DENY)


def _sqlite(tmp_path: Path) -> Iterator[Any]:
    database = Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))
    asyncio.run(database.initialize())
    yield SQLiteMcpServerConfigRepository(database)


def _postgres(_tmp_path: Path) -> Iterator[Any]:
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
            cur.execute("DROP TABLE IF EXISTS mcp_server_configs, peer_key_names")
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
            marks=[pytest.mark.live, pytest.mark.skipif(not DSN, reason="HANGAR_TEST_POSTGRES_DSN is not set")],
        ),
    ]
)
def configs(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    yield from request.param(tmp_path)


class _Runtime:
    """The process's runtime, with the durable configuration repository under test."""

    def __init__(self, configs: Any) -> None:
        self._runtime = get_runtime()
        self.config_repository = configs

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)


def _server() -> dict[str, Any]:
    """A server that is never started: reload only builds, stops and replaces it."""
    return {"mode": "subprocess", "command": ["python", "-c", "pass"]}


def _reset() -> None:
    reset_tool_access_resolver()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in (SERVER, OTHER):
        remove_log_buffer(mcp_server_id)
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    GROUPS.clear()


class _Gateway:
    """A config file, booted, reloaded by the handler bootstrap wires, over a durable fleet record."""

    def __init__(self, path: Path, configs: Any) -> None:
        self.path = path
        self.configs = configs
        self.repository = get_runtime().repository
        self.writer = RepositoryFleetWriter(configs)
        self.handler = ReloadConfigurationHandler(
            self.repository, Mock(), str(path), config_loader=ServerConfigLoader(), groups=GROUPS
        )

    def _write(self, *servers: str) -> None:
        self.path.write_text(yaml.safe_dump({"mcp_servers": {name: _server() for name in servers}}))

    def boot(self, *servers: str) -> None:
        self._write(*servers)
        load_configuration(str(self.path))

    def reload(self, *servers: str) -> dict[str, Any]:
        self._write(*servers)
        return self.handler.handle(ReloadConfigurationCommand(requested_by="test"))

    def push(self, mcp_server_id: str, policy: L7Policy) -> None:
        """What `PUT /api/mcp_servers/{id}/l7_policy` does: the handler, writing the server's row."""
        SetL7PolicyHandler(
            self.repository, Mock(), fleet_writer=self.writer, declared_in_config=server_config.declared_in_config
        ).handle(SetL7PolicyCommand(mcp_server_id=mcp_server_id, policy=policy, source="operator"))

    def row(self, mcp_server_id: str) -> McpServerConfigSnapshot | None:
        return asyncio.run(self.configs.get(mcp_server_id))

    def policy_of(self, mcp_server_id: str) -> L7Policy | None:
        server = self.repository.get(mcp_server_id)
        assert server is not None, f"{mcp_server_id} is not in the fleet"
        return server.l7_policy


@pytest.fixture
def gateway(tmp_path: Path, configs: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Gateway]:
    _reset()
    monkeypatch.setattr(server_config, "get_runtime", lambda: _Runtime(configs))
    gateway = _Gateway(tmp_path / "config.yaml", configs)
    try:
        yield gateway
    finally:
        gateway.writer.close()
        _reset()


def _denies(policy: L7Policy | None, tool: str) -> bool:
    return policy is not None and evaluate_tool(tool, policy.tools, policy.default_action)[0] is ToolAction.DENY


class TestAServerTheFileDroppedAndDeclaresAgain:
    def test_it_starts_with_the_policy_its_row_holds(self, gateway: _Gateway) -> None:
        gateway.boot(SERVER, OTHER)
        gateway.push(SERVER, POLICY)
        assert (row := gateway.row(SERVER)) is not None and row.claimed_by_file is True

        dropped = gateway.reload(OTHER)
        assert dropped["mcp_servers_removed"] == [SERVER]
        assert (row := gateway.row(SERVER)) is not None and row.l7_policy == POLICY.to_wire(), (
            "the dormant row and its policy are still there for the reload that declares it again"
        )

        declared = gateway.reload(SERVER, OTHER)

        assert declared["mcp_servers_added"] == [SERVER]
        assert gateway.policy_of(SERVER) == POLICY
        assert _denies(gateway.policy_of(SERVER), "echo"), "a call outside the policy is refused from the first"
        assert gateway.policy_of(OTHER) is None, "and a server with no stored policy gets none"


class TestAServerAReloadDeclaresForTheFirstTime:
    def test_it_starts_with_the_policy_its_stored_row_holds(self, gateway: _Gateway) -> None:
        gateway.writer.save(
            McpServerConfigSnapshot(
                mcp_server_id=SERVER, mode="subprocess", l7_policy=POLICY.to_wire(), claimed_by_file=True
            )
        )
        gateway.boot(OTHER)

        result = gateway.reload(SERVER, OTHER)

        assert result["mcp_servers_added"] == [SERVER]
        assert gateway.policy_of(SERVER) == POLICY

    def test_a_stored_policy_that_does_not_parse_denies_every_tool(self, gateway: _Gateway) -> None:
        gateway.writer.save(
            McpServerConfigSnapshot(
                mcp_server_id=SERVER, mode="subprocess", l7_policy={"tools": {"deny": "echo"}}, claimed_by_file=True
            )
        )
        gateway.boot(OTHER)

        gateway.reload(SERVER, OTHER)

        assert gateway.policy_of(SERVER) == L7Policy()
        assert _denies(gateway.policy_of(SERVER), "ping")


class TestAStoreThatCannotBeRead:
    def test_refuses_the_reload_before_anything_changes(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gateway.boot(OTHER)
        before = gateway.repository.get(OTHER)

        async def unreadable() -> list[McpServerConfigSnapshot]:
            raise OSError("the configuration store is unreachable")

        monkeypatch.setattr(gateway.configs, "get_all", unreadable)

        with pytest.raises(ConfigurationUnavailableError, match="stored L7 policies could not be read"):
            gateway.reload(SERVER, OTHER)

        assert gateway.repository.get(SERVER) is None
        assert gateway.repository.get(OTHER) is before


class TestTheCarryRule:
    """The rule recovery and a reload share (`carry_stored_l7_policy`)."""

    @pytest.fixture(autouse=True)
    def _carry(self) -> None:
        from mcp_hangar.infrastructure.persistence.recovery_service import carry_stored_l7_policy

        self.carry = carry_stored_l7_policy

    def test_a_policy_already_on_the_built_server_wins(self) -> None:
        # No config key declares an L7 policy today; the guard keeps a future one in charge.
        built = McpServer(mcp_server_id=SERVER, mode="subprocess", command=["python"])
        declared = L7Policy(default_action=ToolAction.ALLOW)
        built.set_l7_policy(declared)

        self.carry(SERVER, built, POLICY.to_wire())

        assert built.l7_policy is declared

    def test_no_stored_policy_leaves_the_server_as_built(self) -> None:
        built = McpServer(mcp_server_id=SERVER, mode="subprocess", command=["python"])

        self.carry(SERVER, built, None)

        assert built.l7_policy is None
