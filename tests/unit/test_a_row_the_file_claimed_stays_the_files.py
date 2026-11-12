"""A row written for a server the file declared is the file's, and does not bring the server back (#2171).

An operator write to a declared server (an L7 push, a `PUT`) writes a row,
which held two jobs: the overlay that carries the L7 policy onto the server the
file builds, and a registration that recovery restored once the file dropped
the server. A row born for a declared server is now claimed by the file:
recovery carries its policy while the file declares the id and skips it once
the file does not, and a create under the id replaces it, carrying the L7
policy and nothing else.

These go through the real handlers writing a SQLite backend, and
`restore_persisted_fleet` reading it back into a fresh repository, as a
restart does.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import (
    CreateMcpServerCommand,
    SetL7PolicyCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    CreateMcpServerHandler,
    SetL7PolicyHandler,
    UpdateMcpServerHandler,
)
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolAction
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services import get_tool_access_resolver
from mcp_hangar.infrastructure.async_bridge import BackgroundLoop
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.recovery_service import RecoveryService
from mcp_hangar.infrastructure.persistence.registry import create_backend
from mcp_hangar.server.bootstrap.persistence import restore_persisted_fleet

POLICY = L7Policy(default_action=ToolAction.ALLOW)


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        return len(events) - 1


class _Gateway:
    """One run of the gateway: the file's servers built first, then the stored fleet restored."""

    def __init__(self, backend: Any, declared: tuple[str, ...]) -> None:
        self.declared = set(declared)
        self.repo = InMemoryMcpServerRepository()
        for name in declared:
            self.repo.add(name, McpServer(mcp_server_id=name, mode="subprocess", command=["python", "-m", name]))
        self.restored = restore_persisted_fleet(
            SimpleNamespace(
                repository=self.repo,
                recovery_service=RecoveryService(
                    database=None,
                    mcp_server_repository=self.repo,
                    config_repository=backend.config_repository(),
                    audit_repository=backend.audit_repository(),
                ),
                persistence_config=SimpleNamespace(enabled=True, auto_recover=True),
            )
        )
        self.writer = RepositoryFleetWriter(backend.config_repository())
        bus = _Bus()
        declared_in_config = self.declared.__contains__
        self.create = CreateMcpServerHandler(
            repository=self.repo, event_bus=bus, fleet_writer=self.writer, declared_in_config=declared_in_config
        )
        self.update = UpdateMcpServerHandler(
            repository=self.repo, event_bus=bus, fleet_writer=self.writer, declared_in_config=declared_in_config
        )
        self.set_l7 = SetL7PolicyHandler(
            repository=self.repo, event_bus=bus, fleet_writer=self.writer, declared_in_config=declared_in_config
        )

    def drop_from_the_file(self, name: str) -> None:
        """What a reload's removal does: out of the fleet, out of the file, its row left."""
        self.declared.discard(name)
        self.repo.remove(name)
        get_tool_access_resolver().remove_mcp_server_policy(name)


@pytest.fixture
def durable(tmp_path):
    backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
    gateways: list[_Gateway] = []

    def boot(*declared: str) -> _Gateway:
        gateway = _Gateway(backend, declared)
        gateways.append(gateway)
        return gateway

    def row(name: str) -> McpServerConfigSnapshot | None:
        loop = BackgroundLoop()
        try:
            rows = loop.run(backend.config_repository().get_all(), 15.0)
        finally:
            loop.close()
        return next((r for r in rows if r.mcp_server_id == name), None)

    try:
        yield SimpleNamespace(boot=boot, row=row, save=lambda snapshot: boot().writer.save(snapshot))
    finally:
        for gateway in gateways:
            gateway.writer.close()
        backend.close()


class TestARowBornForADeclaredServerIsClaimed:
    def test_an_l7_push_to_a_declared_server_claims_the_row_it_creates(self, durable) -> None:
        gateway = durable.boot("upstream")
        gateway.set_l7.handle(SetL7PolicyCommand(mcp_server_id="upstream", policy=POLICY))

        row = durable.row("upstream")
        assert row is not None and row.claimed_by_file is True
        assert row.l7_policy == POLICY.to_wire()

    def test_an_edit_of_a_declared_server_claims_the_row_it_creates(self, durable) -> None:
        gateway = durable.boot("upstream")
        gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="upstream", description="edited at runtime"))

        row = durable.row("upstream")
        assert row is not None and row.claimed_by_file is True

    def test_a_server_the_file_does_not_declare_gets_an_unclaimed_row(self, durable) -> None:
        # A hot-loaded server has no row either; the rule is the declaration, not the missing row.
        gateway = durable.boot()
        gateway.repo.add("loaded", McpServer(mcp_server_id="loaded", mode="subprocess", command=["python"]))
        gateway.set_l7.handle(SetL7PolicyCommand(mcp_server_id="loaded", policy=POLICY))

        row = durable.row("loaded")
        assert row is not None and row.claimed_by_file is False

    def test_a_later_write_keeps_the_claim(self, durable) -> None:
        gateway = durable.boot("upstream")
        gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="upstream", description="edited at runtime"))
        gateway.set_l7.handle(SetL7PolicyCommand(mcp_server_id="upstream", policy=POLICY))

        row = durable.row("upstream")
        assert row is not None and row.claimed_by_file is True
        assert row.description == "edited at runtime" and row.l7_policy == POLICY.to_wire()

    def test_a_row_written_before_the_field_reads_as_unclaimed(self) -> None:
        stored = McpServerConfigSnapshot(mcp_server_id="old", mode="subprocess").to_dict()
        del stored["claimed_by_file"]

        assert McpServerConfigSnapshot.from_dict(stored).claimed_by_file is False
        assert McpServerConfigSnapshot(**{**stored, "claimed_by_file": True}).claimed_by_file is True


class TestARestartReadsTheClaim:
    def test_a_claimed_row_the_file_no_longer_declares_is_not_restored(self, durable) -> None:
        durable.boot("upstream").set_l7.handle(SetL7PolicyCommand(mcp_server_id="upstream", policy=POLICY))

        restarted = durable.boot()

        assert restarted.restored == 0
        assert restarted.repo.get("upstream") is None
        # Left in place: a replica whose file still declares it reads its policy here.
        assert durable.row("upstream") is not None

    def test_a_claimed_row_the_file_still_declares_keeps_its_l7_policy(self, durable) -> None:
        durable.boot("upstream").set_l7.handle(SetL7PolicyCommand(mcp_server_id="upstream", policy=POLICY))

        restarted = durable.boot("upstream")

        assert restarted.repo.get("upstream").l7_policy == POLICY

    def test_an_unclaimed_row_is_restored_as_before(self, durable) -> None:
        durable.boot().create.handle(CreateMcpServerCommand(mcp_server_id="crud", mode="subprocess", command=["x"]))

        restarted = durable.boot()

        assert restarted.restored == 1
        assert restarted.repo.get("crud") is not None


class TestACreateReplacesADormantClaimedRow:
    def _dropped(self, durable) -> _Gateway:
        gateway = durable.boot("upstream")
        gateway.set_l7.handle(SetL7PolicyCommand(mcp_server_id="upstream", policy=POLICY))
        get_tool_access_resolver().set_mcp_server_policy("upstream", _deny_add())
        gateway.drop_from_the_file("upstream")
        return gateway

    def test_it_succeeds_and_carries_the_l7_policy_but_not_the_tool_access_policies(self, durable) -> None:
        gateway = self._dropped(durable)

        result = gateway.create.handle(
            CreateMcpServerCommand(mcp_server_id="upstream", mode="subprocess", command=["python", "-m", "again"])
        )

        assert result == {"mcp_server_id": "upstream", "created": True}
        # On the live server at once, not only after the next restart.
        assert gateway.repo.get("upstream").l7_policy == POLICY
        assert get_tool_access_resolver().get_configured_policy("provider", "upstream") is None
        row = durable.row("upstream")
        assert row is not None
        assert (row.claimed_by_file, row.l7_policy, row.command) == (False, POLICY.to_wire(), ["python", "-m", "again"])

    def test_after_a_restart_too(self, durable) -> None:
        self._dropped(durable)
        restarted = durable.boot()
        assert restarted.repo.get("upstream") is None

        restarted.create.handle(CreateMcpServerCommand(mcp_server_id="upstream", mode="subprocess", command=["x"]))

        assert restarted.repo.get("upstream").l7_policy == POLICY
        assert durable.boot().repo.get("upstream") is not None

    def test_a_dormant_policy_that_does_not_parse_denies_every_tool(self, durable) -> None:
        durable.save(
            McpServerConfigSnapshot(
                mcp_server_id="upstream", mode="subprocess", l7_policy={"tools": {"deny": "add"}}, claimed_by_file=True
            )
        )
        gateway = durable.boot()

        gateway.create.handle(CreateMcpServerCommand(mcp_server_id="upstream", mode="subprocess", command=["x"]))

        assert gateway.repo.get("upstream").l7_policy == L7Policy()

    def test_a_claimed_row_the_file_declares_is_not_replaced(self, durable) -> None:
        durable.save(McpServerConfigSnapshot(mcp_server_id="upstream", mode="subprocess", claimed_by_file=True))
        gateway = durable.boot()
        gateway.declared.add("upstream")  # declared, and not built: a create still may not take its row

        with pytest.raises(ValidationError, match="already exists"):
            gateway.create.handle(CreateMcpServerCommand(mcp_server_id="upstream", mode="subprocess", command=["x"]))

    def test_an_unclaimed_live_row_is_still_refused(self, durable) -> None:
        durable.save(McpServerConfigSnapshot(mcp_server_id="peer", mode="subprocess", command=["x"]))
        gateway = durable.boot()
        gateway.repo.remove("peer")  # a peer created it; this replica has not heard yet

        with pytest.raises(ValidationError, match="already exists"):
            gateway.create.handle(CreateMcpServerCommand(mcp_server_id="peer", mode="subprocess", command=["y"]))


def _deny_add() -> Any:
    from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

    return ToolAccessPolicy(deny_list=("add",))
