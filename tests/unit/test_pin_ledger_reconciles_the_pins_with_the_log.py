"""The pin ledger appends what the configuration's pins changed (#1800)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.application.services.pin_ledger import Reconciled, ledger_state, reconcile_pin_ledger
from mcp_hangar.domain.contracts.event_store import NullEventStore
from mcp_hangar.domain.events import ToolPinRecorded, ToolPinRevoked
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.digest_computation import compute_digest_parts, compute_tool_digest
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.stream_ids import PIN_LEDGER, stream_id_for

A, B = "a" * 64, "b" * 64


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "events.db"


def _replica(db: Path) -> tuple[ToolProjectionRegistry, EventBus]:
    """A process: its own registry and bus, over the one store."""
    return ToolProjectionRegistry(), EventBus(event_store=SQLiteEventStore(db))


def _pin(registry, tool="read", sha=A, tenant=None, upstream=None, server="srv"):
    registry.set_config_pin(server, tool, tenant, ToolDigest(tool_name=tool, sha256=sha, upstream=upstream))


def _ledger(bus: EventBus, server="srv"):
    return bus.event_store.read_stream(stream_id_for(PIN_LEDGER, server))


def test_a_new_pin_is_recorded_in_the_servers_ledger_stream(db):
    registry, bus = _replica(db)
    _pin(registry)

    assert reconcile_pin_ledger(registry, bus) == Reconciled(1, 0)

    (event,) = _ledger(bus)
    assert isinstance(event, ToolPinRecorded)
    assert (event.mcp_server_id, event.tool_name, event.sha256, event.previous_sha256) == ("srv", "read", A, None)
    assert bus.event_store.read_stream("mcp_server:srv") == [], "the server's own stream is not where it goes"


def test_an_unchanged_pin_appends_nothing(db):
    registry, bus = _replica(db)
    _pin(registry)
    reconcile_pin_ledger(registry, bus)

    assert reconcile_pin_ledger(registry, bus) == Reconciled(0, 0)
    assert len(_ledger(bus)) == 1


def test_a_second_replica_reads_the_same_state_and_adds_nothing(db):
    first, first_bus = _replica(db)
    _pin(first)
    reconcile_pin_ledger(first, first_bus)

    second, second_bus = _replica(db)
    _pin(second)

    assert reconcile_pin_ledger(second, second_bus) == Reconciled(0, 0)
    assert ledger_state(_ledger(second_bus)) == {("read", None): (A, None)}


def test_a_changed_digest_names_the_one_it_replaced(db):
    registry, bus = _replica(db)
    _pin(registry, sha=A)
    reconcile_pin_ledger(registry, bus)
    _pin(registry, sha=B)

    reconcile_pin_ledger(registry, bus)

    recorded = [e for e in _ledger(bus) if isinstance(e, ToolPinRecorded)]
    assert [(e.sha256, e.previous_sha256) for e in recorded] == [(A, None), (B, A)]


def test_a_changed_upstream_binding_is_a_change(db):
    registry, bus = _replica(db)
    _pin(registry)
    reconcile_pin_ledger(registry, bus)
    _pin(registry, upstream="stdio:node:aa")

    assert reconcile_pin_ledger(registry, bus) == Reconciled(1, 0)
    assert _ledger(bus)[-1].upstream == "stdio:node:aa"


def test_a_pin_the_file_no_longer_declares_is_revoked_once(db):
    registry, bus = _replica(db)
    _pin(registry)
    reconcile_pin_ledger(registry, bus)

    gone, _ = ToolProjectionRegistry(), None
    assert reconcile_pin_ledger(gone, bus) == Reconciled(0, 1)
    assert reconcile_pin_ledger(gone, bus) == Reconciled(0, 0)

    revoked = _ledger(bus)[-1]
    assert isinstance(revoked, ToolPinRevoked) and revoked.sha256 == A
    assert ledger_state(_ledger(bus)) == {}


def test_a_tenants_pin_and_the_all_tenants_pin_are_separate_lines(db):
    registry, bus = _replica(db)
    _pin(registry)
    _pin(registry, sha=B, tenant="t1")

    reconcile_pin_ledger(registry, bus)

    assert ledger_state(_ledger(bus)) == {("read", None): (A, None), ("read", "t1"): (B, None)}


def test_the_structure_and_description_digests_are_recorded_when_the_tool_is_served(db):
    registry, bus = _replica(db)
    tool = ToolSchema(name="read", description="Read a file", input_schema={"type": "object"})
    registry.build_from_tools("srv", [tool])
    whole = compute_tool_digest(tool.to_dict()).sha256
    _pin(registry, sha=whole)
    _pin(registry, tool="unserved", sha=B)

    reconcile_pin_ledger(registry, bus)

    by_tool = {e.tool_name: e for e in _ledger(bus)}
    structure, description = compute_digest_parts(tool.to_dict())
    assert (by_tool["read"].structure_sha256, by_tool["read"].description_sha256) == (structure, description)
    assert (by_tool["unserved"].structure_sha256, by_tool["unserved"].description_sha256) == (None, None)


def test_the_two_halves_change_independently():
    base = {"name": "t", "description": "d", "inputSchema": {"type": "object"}}
    reworded = {**base, "description": "d2"}
    reshaped = {**base, "inputSchema": {"type": "object", "properties": {"x": {}}}}

    assert compute_digest_parts(reworded)[0] == compute_digest_parts(base)[0]
    assert compute_digest_parts(reworded)[1] != compute_digest_parts(base)[1]
    assert compute_digest_parts(reshaped)[0] != compute_digest_parts(base)[0]
    assert compute_digest_parts(reshaped)[1] == compute_digest_parts(base)[1]
    assert compute_digest_parts({"name": "t"})[1] is None


def test_no_log_means_no_history_and_no_noise():
    registry = ToolProjectionRegistry()
    _pin(registry)
    bus = MagicMock()
    bus.event_store = NullEventStore()

    assert reconcile_pin_ledger(registry, bus) == Reconciled(0, 0)
    bus.publish.assert_not_called()


class TestTheDigestParts:
    def test_a_tool_without_a_name_is_refused(self):
        with pytest.raises(ValueError, match="name"):
            compute_digest_parts({"description": "d"})
        with pytest.raises(ValueError, match="name"):
            compute_digest_parts({"name": ""})

    def test_the_output_schema_is_part_of_the_structure(self):
        base = {"name": "t", "inputSchema": {"type": "object"}}
        with_output = {**base, "outputSchema": {"type": "object", "properties": {"r": {}}}}

        assert compute_digest_parts(with_output)[0] != compute_digest_parts(base)[0]

    @pytest.mark.parametrize("description", [None, "", 5, ["x"]])
    def test_a_missing_or_unusable_description_has_no_digest(self, description):
        assert compute_digest_parts({"name": "t", "description": description})[1] is None
