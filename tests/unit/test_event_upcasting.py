"""Unit tests for event upcasting and versioned event serialization."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

import pytest

from mcp_hangar.domain.events import McpServerStarted
from mcp_hangar.infrastructure.persistence.event_serializer import EVENT_VERSION_MAP
from mcp_hangar.infrastructure.persistence.event_upcaster import IEventUpcaster
from mcp_hangar.infrastructure.persistence import EventSerializer, SQLiteEventStore, UpcasterChain


class McpServerStartedV1ToV2(IEventUpcaster):
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
        return {**data, "tags": []}


class McpServerStartedV2ToV3(IEventUpcaster):
    @property
    def event_type(self) -> str:
        return "McpServerStarted"

    @property
    def from_version(self) -> int:
        return 2

    @property
    def to_version(self) -> int:
        return 3

    def upcast(self, data: dict[str, object]) -> dict[str, object]:
        # Example evolution: rename tools_count -> tools_total
        tools_count = data.get("tools_count")
        updated = dict(data)
        if tools_count is not None:
            updated["tools_total"] = tools_count
        return updated


def test_upcaster_chain_single_version_jump():
    chain = UpcasterChain()
    chain.register(McpServerStartedV1ToV2())

    version, payload = chain.upcast("McpServerStarted", 1, {"mcp_server_id": "x"}, current_version=2)

    assert version == 2
    assert payload == {"mcp_server_id": "x", "tags": []}


def test_upcaster_chain_multi_version_jump():
    chain = UpcasterChain()
    chain.register(McpServerStartedV1ToV2())
    chain.register(McpServerStartedV2ToV3())

    version, payload = chain.upcast("McpServerStarted", 1, {"mcp_server_id": "x", "tools_count": 5}, current_version=3)

    assert version == 3
    assert payload["mcp_server_id"] == "x"
    assert payload["tags"] == []
    assert payload["tools_total"] == 5


def test_upcaster_chain_no_upcaster_needed():
    chain = UpcasterChain()

    version, payload = chain.upcast("McpServerStarted", 3, {"mcp_server_id": "x"}, current_version=3)

    assert version == 3
    assert payload == {"mcp_server_id": "x"}


def test_upcaster_chain_unknown_event_type_passthrough():
    chain = UpcasterChain()

    version, payload = chain.upcast("SomeUnknownEvent", 1, {"a": 1}, current_version=5)

    assert version == 1
    assert payload == {"a": 1}


def test_serializer_adds_version_on_serialize(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(EVENT_VERSION_MAP, "McpServerStarted", 2)

    serializer = EventSerializer()
    event = McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=3, startup_duration_ms=10.0)

    event_type, json_data = serializer.serialize(event)

    assert event_type == "McpServerStarted"
    payload = json.loads(json_data)
    assert payload["_version"] == 2


def test_serializer_backward_compat_missing_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(EVENT_VERSION_MAP, "McpServerStarted", 1)

    serializer = EventSerializer()

    raw_payload = {
        "mcp_server_id": "math",
        "mode": "subprocess",
        "tools_count": 3,
        "startup_duration_ms": 10.0,
    }

    event = serializer.deserialize("McpServerStarted", json.dumps(raw_payload))

    assert isinstance(event, McpServerStarted)
    assert event.mcp_server_id == "math"
    assert event.tools_count == 3


def test_serializer_applies_upcasters_on_deserialize(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(EVENT_VERSION_MAP, "McpServerStarted", 2)

    chain = UpcasterChain()
    chain.register(McpServerStartedV1ToV2())
    serializer = EventSerializer(upcaster_chain=chain)

    # Old persisted payload version
    raw_payload = {
        "_version": 1,
        "mcp_server_id": "math",
        "mode": "subprocess",
        "tools_count": 3,
        "startup_duration_ms": 10.0,
        "tags": [],
    }
    # Simulate v1 missing "tags" by removing it
    raw_payload.pop("tags")

    event = serializer.deserialize("McpServerStarted", json.dumps(raw_payload))

    assert isinstance(event, McpServerStarted)
    assert event.mcp_server_id == "math"


def test_end_to_end_old_event_in_sqlite_store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(EVENT_VERSION_MAP, "McpServerStarted", 2)

    chain = UpcasterChain()
    chain.register(McpServerStartedV1ToV2())
    serializer = EventSerializer(upcaster_chain=chain)

    db_path = tmp_path / "events.db"
    store = SQLiteEventStore(db_path, serializer=serializer)

    # Insert old event directly (no _version)
    conn = sqlite3.connect(str(db_path))
    try:
        payload = json.dumps(
            {
                "mcp_server_id": "math",
                "mode": "subprocess",
                "tools_count": 1,
                "startup_duration_ms": 1.0,
            }
        )
        conn.execute(
            "INSERT INTO streams (stream_id, version, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("mcp_server:math", 0, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO events (stream_id, stream_version, event_type, data, created_at) VALUES (?, ?, ?, ?, ?)",
            ("mcp_server:math", 0, "McpServerStarted", payload, "2020-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    events = store.read_stream("mcp_server:math")

    assert len(events) == 1
    assert isinstance(events[0], McpServerStarted)
    ev = cast(McpServerStarted, events[0])
    assert ev.mcp_server_id == "math"
