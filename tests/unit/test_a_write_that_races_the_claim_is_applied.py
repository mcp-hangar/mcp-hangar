"""An operator write that read a row just before the claim pass marked it is applied (#2229).

The claim pass marks a row and bumps its version in one statement. An L7 push
or an edit that read the row before the mark used to be refused with
`ConcurrentModificationError`, which the REST layer reports as a 500. The mark
changes nothing the write cares about, so the write is applied on the row as it
now is. Any other change between the read and the write -- another edit, a
delete -- still refuses it.

The race is made deterministic with a writer that runs the competing change
between the handler's read and its write, against a real SQLite backend.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import SetL7PolicyCommand, UpdateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import SetL7PolicyHandler, UpdateMcpServerHandler
from mcp_hangar.domain.contracts.persistence import ConcurrentModificationError, McpServerConfigSnapshot
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolAction
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.async_bridge import BackgroundLoop
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.registry import create_backend

POLICY = L7Policy(default_action=ToolAction.ALLOW)
NAME = "upstream"


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        return len(events) - 1


class _RacingWriter(RepositoryFleetWriter):
    """A fleet writer that lets *meanwhile* run once, just before the first versioned write."""

    meanwhile: Callable[[], None] | None = None

    def save_versioned(self, snapshot: McpServerConfigSnapshot, expected_version: int | None, *args: Any) -> None:
        competing, self.meanwhile = self.meanwhile, None
        if competing is not None:
            competing()
        super().save_versioned(snapshot, expected_version, *args)


@pytest.fixture
def gateway(tmp_path):
    backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
    repo = InMemoryMcpServerRepository()
    repo.add(NAME, McpServer(mcp_server_id=NAME, mode="subprocess", command=["python", "-m", NAME]))
    writer = _RacingWriter(backend.config_repository())
    peer = RepositoryFleetWriter(backend.config_repository())
    # A row the API wrote, which the file has since taken over: unclaimed until the pass runs.
    peer.save(McpServerConfigSnapshot(mcp_server_id=NAME, mode="subprocess", command=["python", "-m", NAME]))

    def row() -> McpServerConfigSnapshot | None:
        loop = BackgroundLoop()
        try:
            rows = loop.run(backend.config_repository().get_all(), 15.0)
        finally:
            loop.close()
        return next((r for r in rows if r.mcp_server_id == NAME), None)

    handlers = {
        "set_l7": SetL7PolicyHandler(
            repository=repo, event_bus=_Bus(), fleet_writer=writer, declared_in_config=lambda sid: True
        ),
        "update": UpdateMcpServerHandler(
            repository=repo, event_bus=_Bus(), fleet_writer=writer, declared_in_config=lambda sid: True
        ),
    }
    try:
        yield writer, peer, row, handlers
    finally:
        writer.close()
        peer.close()
        backend.close()


def test_an_l7_push_that_read_the_row_before_the_claim_is_applied(gateway) -> None:
    writer, peer, row, handlers = gateway
    writer.meanwhile = lambda: peer.claim_for_file(NAME)

    handlers["set_l7"].handle(SetL7PolicyCommand(mcp_server_id=NAME, policy=POLICY))

    stored = row()
    assert stored is not None
    assert stored.claimed_by_file is True
    assert stored.l7_policy == POLICY.to_wire()


def test_an_edit_that_read_the_row_before_the_claim_is_applied(gateway) -> None:
    writer, peer, row, handlers = gateway
    writer.meanwhile = lambda: peer.claim_for_file(NAME)

    handlers["update"].handle(UpdateMcpServerCommand(mcp_server_id=NAME, description="edited at runtime"))

    stored = row()
    assert stored is not None
    assert stored.claimed_by_file is True
    assert stored.description == "edited at runtime"


def test_another_edit_between_the_read_and_the_write_still_refuses_it(gateway) -> None:
    writer, peer, row, handlers = gateway

    def a_peer_edits_the_row() -> None:
        current = row()
        assert current is not None
        peer.save(dataclasses.replace(current, description="a peer's edit"))

    writer.meanwhile = a_peer_edits_the_row

    with pytest.raises(ConcurrentModificationError):
        handlers["set_l7"].handle(SetL7PolicyCommand(mcp_server_id=NAME, policy=POLICY))

    stored = row()
    assert stored is not None
    assert stored.description == "a peer's edit"
    assert stored.l7_policy is None


def test_a_delete_between_the_read_and_the_write_still_refuses_it(gateway) -> None:
    writer, peer, row, handlers = gateway
    writer.meanwhile = lambda: peer.delete(NAME)

    with pytest.raises(ConcurrentModificationError):
        handlers["set_l7"].handle(SetL7PolicyCommand(mcp_server_id=NAME, policy=POLICY))

    assert row() is None
