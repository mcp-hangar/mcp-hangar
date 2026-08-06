"""Events written under a pre-rename name must still reach their handlers.

The `provider` -> `mcp_server` rename landed 2026-04-22, after v1.0.1. Eight
releases shipped before it, so any event store from those versions holds rows
typed `ProviderStarted`, `ProviderDiscovered` and so on.

Two independent layers dropped those events on the floor, silently:

* the serializer mapped the legacy type names to the deprecated alias classes,
  so a legacy row reconstructed into `ProviderStarted` rather than
  `McpServerStarted`, and looked its schema version up under a key no upcaster
  is registered against;
* the event bus dispatched on the exact class, so a `ProviderStarted` -- a
  *subclass* of `McpServerStarted` -- reached none of the handlers registered
  against the modern class. Not an error, not a warning: a `handlers_count=0`
  debug line.

Together that meant replaying pre-rename history was a no-op for every consumer.
These tests cover both layers, and the inventory check keeps them honest as the
alias list changes.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain import events as events_pkg
from mcp_hangar.domain.events import (
    LEGACY_EVENT_TYPE_NAMES,
    DomainEvent,
    McpServerDiscovered,
    McpServerStarted,
    ProviderDiscovered,
    ProviderStarted,
    canonical_event_type,
)
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import EventSerializer, UpcasterChain
from mcp_hangar.infrastructure.persistence.event_serializer import EVENT_TYPE_MAP, EVENT_VERSION_MAP
from mcp_hangar.infrastructure.persistence.event_upcaster import IEventUpcaster


def _alias_inventory_from_source() -> dict[str, str]:
    """Every alias declared in the package's source, read from the AST.

    Deliberately not derived from `vars(events_pkg)`: `LEGACY_EVENT_TYPE_NAMES`
    is computed that way, so comparing the two would be a tautology -- the test
    would agree with the code no matter what either did. Reading the source
    gives an independent answer, and it also catches an alias that was added but
    never re-exported, which the runtime view cannot see at all.
    """
    pkg_dir = pathlib.Path(events_pkg.__file__).parent
    # DomainEvent itself is excluded: subclassing it is what makes something an
    # event, not what makes it an alias of another one.
    event_class_names = {
        obj.__name__
        for obj in vars(events_pkg).values()
        if inspect.isclass(obj) and issubclass(obj, DomainEvent) and obj is not DomainEvent
    }
    inventory: dict[str, str] = {}
    for path in sorted(pkg_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            # Subclass alias: `class ProviderStarted(McpServerStarted): ...`
            if isinstance(node, ast.ClassDef) and node.bases:
                base = node.bases[0]
                if isinstance(base, ast.Name) and base.id in event_class_names and base.id != node.name:
                    inventory[node.name] = base.id
            # Assignment alias: `ProviderHotLoaded = McpServerHotLoaded`
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                if node.value.id in event_class_names:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            inventory[target.id] = node.value.id
    return inventory


class TestTheMappingCoversEveryAlias:
    """A missed alias is an event type that silently keeps the old behaviour."""

    def test_every_alias_has_a_canonical_name(self):
        assert _alias_inventory_from_source() == LEGACY_EVENT_TYPE_NAMES

    def test_there_are_aliases_to_map(self):
        """Guards against the derivation quietly returning nothing."""
        assert len(LEGACY_EVENT_TYPE_NAMES) >= 15

    @pytest.mark.parametrize("legacy", sorted(LEGACY_EVENT_TYPE_NAMES))
    def test_the_canonical_name_is_not_itself_an_alias(self, legacy):
        """One hop, not a chain -- otherwise resolution depends on iteration order."""
        assert canonical_event_type(legacy) not in LEGACY_EVENT_TYPE_NAMES

    def test_a_current_name_resolves_to_itself(self):
        assert canonical_event_type("McpServerStarted") == "McpServerStarted"
        assert canonical_event_type("SomethingNobodyDefined") == "SomethingNobodyDefined"


class TestTheSerializerResolvesLegacyRows:
    LEGACY_ROW = json.dumps(
        {
            "_version": 1,
            "mcp_server_id": "p1",
            "mode": "subprocess",
            "tools_count": 3,
            "startup_duration_ms": 1.5,
            "event_id": "stored-id",
            "occurred_at": 1234.5,
        }
    )

    def test_a_pre_rename_row_reconstructs_into_the_modern_class(self):
        restored = EventSerializer().deserialize("ProviderStarted", self.LEGACY_ROW)
        assert type(restored) is McpServerStarted

    def test_the_stored_identity_survives(self):
        """Replay must not re-date history or mint a new id."""
        restored = EventSerializer().deserialize("ProviderStarted", self.LEGACY_ROW)
        assert restored.event_id == "stored-id"
        assert restored.occurred_at == 1234.5

    def test_the_payload_survives(self):
        restored = EventSerializer().deserialize("ProviderStarted", self.LEGACY_ROW)
        assert restored.mcp_server_id == "p1"
        assert restored.tools_count == 3

    @pytest.mark.parametrize("legacy", sorted(LEGACY_EVENT_TYPE_NAMES))
    def test_every_legacy_name_resolves_to_a_registered_class(self, legacy):
        """A legacy name whose canonical form is unregistered still fails on replay."""
        canonical = canonical_event_type(legacy)
        if canonical not in EVENT_TYPE_MAP:
            pytest.skip(f"{canonical} is not persisted by the serializer")
        assert EVENT_TYPE_MAP[canonical].__name__ == canonical

    def test_an_alias_instance_is_written_under_the_current_name(self):
        """Otherwise the store keeps accumulating rows that need translating back."""
        event = ProviderDiscovered(mcp_server_name="p1", source_type="fs", mode="subprocess", fingerprint="abc")
        event_type, _ = EventSerializer().serialize(event)
        assert event_type == "McpServerDiscovered"

    def test_an_unknown_type_still_raises(self):
        from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializationError

        with pytest.raises(EventSerializationError):
            EventSerializer().deserialize("NoSuchEvent", "{}")


class _StartedV1ToV2(IEventUpcaster):
    """Registered against the modern name, as every upcaster is."""

    @property
    def event_type(self) -> str:
        return "McpServerStarted"

    @property
    def from_version(self) -> int:
        return 1

    @property
    def to_version(self) -> int:
        return 2

    def upcast(self, data: dict[str, object]) -> dict[str, object]:
        return {**data, "mode": "upcasted"}


class TestUpcastersFireForLegacyRows:
    """The version lookup keyed on the stored name, so legacy rows skipped upcasting.

    This is the part that would have bitten later rather than now: with the
    alias name absent from `EVENT_VERSION_MAP`, `get_current_version` returned
    the default 1, so `version < current_version` was never true and no
    upcaster ran -- for exactly the rows written by the oldest installs, which
    are the ones most likely to need one.
    """

    def test_a_legacy_row_is_upcast_under_the_modern_name(self, monkeypatch):
        monkeypatch.setitem(EVENT_VERSION_MAP, "McpServerStarted", 2)
        chain = UpcasterChain()
        chain.register(_StartedV1ToV2())

        restored = EventSerializer(chain).deserialize("ProviderStarted", TestTheSerializerResolvesLegacyRows.LEGACY_ROW)

        assert restored.mode == "upcasted", "the upcaster registered on the modern name did not fire"


class TestTheBusDeliversToBaseClassHandlers:
    def test_an_alias_event_reaches_the_modern_handler(self):
        bus, seen = EventBus(), []
        bus.subscribe(McpServerDiscovered, lambda event: seen.append(event), kind=HandlerKind.EFFECT)
        bus.publish(ProviderDiscovered(mcp_server_name="p1", source_type="fs", mode="subprocess", fingerprint="abc"))
        assert len(seen) == 1

    def test_a_modern_event_does_not_reach_an_alias_handler(self):
        """Inheritance runs one way; a base-class event is not an alias event."""
        bus, seen = EventBus(), []
        bus.subscribe(ProviderDiscovered, lambda event: seen.append(event), kind=HandlerKind.EFFECT)
        bus.publish(McpServerDiscovered(mcp_server_name="p1", source_type="fs", mode="subprocess", fingerprint="abc"))
        assert seen == []

    def test_a_handler_registered_on_both_classes_fires_once(self):
        """MRO delivery must not turn one event into two calls."""
        bus, seen = EventBus(), []

        def handler(event):
            seen.append(event)

        bus.subscribe(McpServerDiscovered, handler, kind=HandlerKind.EFFECT)
        bus.subscribe(ProviderDiscovered, handler, kind=HandlerKind.EFFECT)
        bus.publish(ProviderDiscovered(mcp_server_name="p1", source_type="fs", mode="subprocess", fingerprint="abc"))
        assert len(seen) == 1

    def test_subscribe_to_all_still_runs_after_the_specific_handlers(self):
        bus, order = EventBus(), []
        bus.subscribe_to_all(lambda event: order.append("all"), kind=HandlerKind.EFFECT)
        bus.subscribe(McpServerStarted, lambda event: order.append("specific"), kind=HandlerKind.EFFECT)
        bus.publish(McpServerStarted(mcp_server_id="p1", mode="subprocess", tools_count=0, startup_duration_ms=0.0))
        assert order == ["specific", "all"]

    def test_subscribe_to_all_receives_an_alias_event_once(self):
        bus, seen = EventBus(), []
        bus.subscribe_to_all(lambda event: seen.append(event), kind=HandlerKind.EFFECT)
        bus.publish(ProviderStarted(mcp_server_id="p1", mode="subprocess", tools_count=0, startup_duration_ms=0.0))
        assert len(seen) == 1

    def test_an_unrelated_event_type_is_not_delivered(self):
        """The MRO walk must not turn dispatch into a broadcast."""
        bus, seen = EventBus(), []
        bus.subscribe(McpServerDiscovered, lambda event: seen.append(event), kind=HandlerKind.EFFECT)
        bus.publish(McpServerStarted(mcp_server_id="p1", mode="subprocess", tools_count=0, startup_duration_ms=0.0))
        assert seen == []


def test_a_pre_rename_row_replays_all_the_way_to_a_handler():
    """The two layers in one path: this is the scenario that was broken end to end."""
    bus, seen = EventBus(), []
    bus.subscribe(McpServerStarted, lambda event: seen.append(event), kind=HandlerKind.EFFECT)

    restored = EventSerializer().deserialize("ProviderStarted", TestTheSerializerResolvesLegacyRows.LEGACY_ROW)
    bus.publish(restored)

    assert len(seen) == 1, "a row written before the rename still does not reach its handler"
    assert seen[0].event_id == "stored-id"
