"""`ToolWithdrawn` / `ToolRestored` say which overlay was written (#1140).

The withdrawal overlay has been keyed on `(mcp_server, kind, name)` since
2.13.0, but the events only carried the name, so a consumer rebuilding from
the log replayed every withdrawal as a tool one. Both events now carry `kind`
at schema version 2. Rows written by an older gateway have no `kind` and were
always tool withdrawals, so they still deserialize -- as `"tool"`.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from mcp_hangar.domain.events import ToolRestored, ToolWithdrawn
from mcp_hangar.infrastructure.persistence.event_serializer import (
    EVENT_TYPE_MAP,
    EventSerializer,
    get_current_version,
)


def test_the_version_map_agrees_with_every_dataclass():
    """A bump on one side without the other is the drift that breaks replay."""
    drift = {}
    for name, cls in EVENT_TYPE_MAP.items():
        field = {f.name: f for f in dataclasses.fields(cls)}.get("schema_version")
        if field is not None and field.default != get_current_version(name):
            drift[name] = (field.default, get_current_version(name))
    assert drift == {}, f"dataclass vs EVENT_VERSION_MAP: {drift}"


@pytest.mark.parametrize("event_class", [ToolWithdrawn, ToolRestored])
def test_kind_round_trips(event_class):
    serializer = EventSerializer()
    event = event_class(tenant_id="tenant:a", mcp_server="srv", tool="search", kind="prompt")

    event_type, data = serializer.serialize(event)
    restored = serializer.deserialize(event_type, data)

    assert json.loads(data)["_version"] == 2
    assert restored.kind == "prompt"
    assert restored.schema_version == 2


@pytest.mark.parametrize("event_class", [ToolWithdrawn, ToolRestored])
def test_a_v1_row_without_kind_replays_as_a_tool(event_class):
    """Written by a gateway older than this field: the only kind it could withdraw."""
    v1_row = {"_version": 1, "tenant_id": None, "mcp_server": "srv", "tool": "search", "schema_version": 1}

    restored = EventSerializer().deserialize(event_class.__name__, json.dumps(v1_row))

    assert isinstance(restored, event_class)
    assert restored.kind == "tool"
    assert restored.tool == "search"
