"""Replaying a stream must not re-mint an event's identity.

Two invariants that nothing asserted before:

* **`event_id` survives replay.** A consumer that de-duplicates on event id --
  any at-least-once projection or audit sink -- silently reprocesses everything
  if replay hands back fresh ids.
* **`occurred_at` survives replay.** A fresh timestamp re-dates history to
  whenever the stream happened to be read, which turns an audit trail into a
  record of when someone last restarted the process.

Both restoration paths (the event serializer and the event-sourced repository)
used to patch these two attributes in place, three lines each, duplicated. They
now go through `DomainEvent.rehydrate`, so the behaviour is pinned once here.
"""

import pytest

from mcp_hangar.domain.events import McpServerStarted, McpServerStopped
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer


def _started(**overrides):
    data = {
        "mcp_server_id": "payments",
        "mode": "subprocess",
        "tools_count": 3,
        "startup_duration_ms": 12.5,
    }
    data.update(overrides)
    return McpServerStarted(**data)


class TestRehydrateRestoresIdentity:
    def test_stored_id_and_timestamp_are_restored(self):
        event = McpServerStarted.rehydrate(
            "stored-id-1",
            1_700_000_000.0,
            mcp_server_id="payments",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=12.5,
        )
        assert event.event_id == "stored-id-1"
        assert event.occurred_at == 1_700_000_000.0

    def test_payload_is_still_applied(self):
        """Restoring identity must not swallow the event's own fields."""
        event = McpServerStarted.rehydrate(
            "stored-id-2",
            1.0,
            mcp_server_id="payments",
            mode="docker",
            tools_count=7,
            startup_duration_ms=99.0,
        )
        assert (event.mcp_server_id, event.mode, event.tools_count) == ("payments", "docker", 7)

    def test_none_identity_mints_a_fresh_one(self):
        """A brand-new event, not a replayed one, still gets an id and a time."""
        event = McpServerStarted.rehydrate(
            None,
            None,
            mcp_server_id="payments",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=1.0,
        )
        assert event.event_id
        assert event.occurred_at > 0

    def test_identity_is_not_shared_between_fresh_events(self):
        a = McpServerStarted.rehydrate(None, None, mcp_server_id="a", mode="m", tools_count=0, startup_duration_ms=0.0)
        b = McpServerStarted.rehydrate(None, None, mcp_server_id="b", mode="m", tools_count=0, startup_duration_ms=0.0)
        assert a.event_id != b.event_id


class TestSerializerRoundTripKeepsIdentity:
    @pytest.fixture
    def serializer(self):
        return EventSerializer()

    def test_round_trip_preserves_event_id(self, serializer):
        original = _started()
        restored = serializer.deserialize(*serializer.serialize(original))
        assert restored.event_id == original.event_id, (
            "replay minted a new event_id; every consumer de-duplicating on it would reprocess the whole stream"
        )

    def test_round_trip_preserves_occurred_at(self, serializer):
        original = _started()
        restored = serializer.deserialize(*serializer.serialize(original))
        assert restored.occurred_at == original.occurred_at, (
            "replay re-dated the event to read time, which turns an audit trail into a record of the last restart"
        )

    def test_round_trip_preserves_the_payload(self, serializer):
        original = _started(mcp_server_id="billing", tools_count=9)
        restored = serializer.deserialize(*serializer.serialize(original))
        assert restored.mcp_server_id == "billing"
        assert restored.tools_count == 9

    def test_a_second_event_type_round_trips_too(self, serializer):
        """One passing type could be a coincidence of that type's shape."""
        original = McpServerStopped(mcp_server_id="payments", reason="idle")
        restored = serializer.deserialize(*serializer.serialize(original))
        assert isinstance(restored, McpServerStopped)
        assert (restored.event_id, restored.occurred_at) == (original.event_id, original.occurred_at)
        assert restored.reason == "idle"
