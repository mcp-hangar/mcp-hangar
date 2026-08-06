"""An event says which instance produced it, and a stored one keeps saying it.

With one replica this is an audit convenience. With three it is the difference
between a tailer that works and one that cannot: a replica publishes to its own
handlers *and* appends to the shared log, then tails that log back. Without a
producer on the row it cannot tell its own append from a peer's.

The load-bearing case here is the oldest one: a row written before this field
existed must not deserialize as "produced by whoever is reading it". That would
make a tailer skip history as its own work, silently, which is the failure this
codebase produces most reliably.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.events.producer import (
    UNKNOWN_PRODUCER,
    current_instance_id,
    set_instance_id,
)
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer


@dataclass
class _ThingHappened(DomainEvent):
    """A minimal event, so these tests do not depend on any real one's payload."""

    thing: str = "x"


@pytest.fixture
def restore_identity():
    """Identity is process-wide; put it back so ordering cannot matter."""
    import mcp_hangar.domain.events.producer as producer

    before = producer._instance_id
    yield
    producer._instance_id = before


class TestAnEventKnowsWhoProducedIt:
    def test_a_fresh_event_carries_this_instance(self, restore_identity) -> None:
        set_instance_id("gateway-a")

        assert _ThingHappened(thing="t").produced_by == current_instance_id()

    def test_the_producer_survives_a_round_trip(self, restore_identity) -> None:
        set_instance_id("gateway-a")
        serializer = EventSerializer()

        event_type, data = serializer.serialize(_ThingHappened(thing="t"))
        restored = serializer.deserialize(event_type, data)

        assert restored.produced_by == current_instance_id()

    def test_a_peers_event_is_not_re_attributed_to_the_reader(self, restore_identity) -> None:
        # The whole point. B reads a row A wrote; it must still say A.
        set_instance_id("gateway-a")
        serializer = EventSerializer()
        producer_a = current_instance_id()
        event_type, data = serializer.serialize(_ThingHappened(thing="t"))

        set_instance_id("gateway-b")
        restored = EventSerializer().deserialize(event_type, data)

        assert restored.produced_by == producer_a
        assert restored.produced_by != current_instance_id()


class TestARowStoredBeforeThisFieldExisted:
    def test_it_still_deserializes(self, restore_identity) -> None:
        # Schema evolution, on the persisted surface ADR-018 calls a
        # compatibility surface. A stored row has no `produced_by` key at all.
        stored = json.dumps({"_version": 1, "event_id": "e-1", "occurred_at": 1.0, "thing": "t"})

        restored = EventSerializer().deserialize("_ThingHappened", stored)

        assert restored.event_id == "e-1"
        assert restored.thing == "t"  # type: ignore[attr-defined]

    def test_it_does_not_claim_the_reader_produced_it(self, restore_identity) -> None:
        set_instance_id("gateway-a")
        stored = json.dumps({"_version": 1, "event_id": "e-1", "occurred_at": 1.0, "thing": "t"})

        restored = EventSerializer().deserialize("_ThingHappened", stored)

        assert restored.produced_by == UNKNOWN_PRODUCER
        assert restored.produced_by != current_instance_id()

    def test_unknown_is_never_a_minted_identity(self, restore_identity) -> None:
        # `UNKNOWN_PRODUCER` reads as "not mine" only while no live instance can
        # ever be called that.
        assert set_instance_id(UNKNOWN_PRODUCER) != UNKNOWN_PRODUCER
        assert set_instance_id(None) != UNKNOWN_PRODUCER


class TestIdentityIsMintedRatherThanConfigured:
    def test_the_same_label_twice_is_two_instances(self, restore_identity) -> None:
        # Three replicas rolled from one ConfigMap. If the label were the
        # identity they would each treat the others' events as their own and
        # the tail would go quiet with every health check green.
        assert set_instance_id("gateway") != set_instance_id("gateway")

    def test_the_label_is_still_recognisable_in_the_identity(self, restore_identity) -> None:
        # Minting must not cost the operator the ability to tell which pod
        # wrote a row.
        assert set_instance_id("hangar-7f9c4d").startswith("hangar-7f9c4d")

    def test_an_unset_identity_is_still_unique_per_process(self, restore_identity) -> None:
        # An embedded or test use that never bootstraps gets a real id, not a
        # placeholder shared with every other process.
        import mcp_hangar.domain.events.producer as producer

        producer._instance_id = None
        minted = current_instance_id()

        assert minted and minted == current_instance_id()

    def test_bootstrap_labels_the_instance_from_the_environment(self, restore_identity, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import _init_instance_identity

        monkeypatch.setenv("HANGAR_INSTANCE_LABEL", "hangar-gw-0")

        assert _init_instance_identity().startswith("hangar-gw-0")

    def test_without_the_variable_it_falls_back_to_the_hostname(self, restore_identity, monkeypatch) -> None:
        # Under Kubernetes the hostname is the pod name, which is the label an
        # operator would have chosen anyway.
        import socket

        from mcp_hangar.server.bootstrap import _init_instance_identity

        monkeypatch.delenv("HANGAR_INSTANCE_LABEL", raising=False)

        assert _init_instance_identity().startswith(socket.gethostname())


class TestTheFieldDoesNotDisturbWhatWasAlreadyThere:
    def test_two_events_with_the_same_payload_still_compare_equal(self, restore_identity) -> None:
        # `event_id` and `occurred_at` are `compare=False` so payload equality
        # is what event assertions across this suite rely on. A third identity
        # field that compared would break them all.
        set_instance_id("gateway-a")
        one = _ThingHappened(thing="t")
        set_instance_id("gateway-b")

        assert one == _ThingHappened(thing="t")

    def test_the_stored_identity_still_wins_over_a_fresh_one(self, restore_identity) -> None:
        stored = json.dumps(
            {"_version": 1, "event_id": "e-9", "occurred_at": 7.5, "produced_by": "gateway-z", "thing": "t"}
        )

        restored = EventSerializer().deserialize("_ThingHappened", stored)

        assert (restored.event_id, restored.occurred_at, restored.produced_by) == ("e-9", 7.5, "gateway-z")
