# Event Sourcing

MCP Hangar persists domain events in an append-only Event Store. This supports auditing, projections, and rebuilding state from history.

## Persistence format

Events are serialized as JSON and stored with:

- `stream_id` (e.g. `provider:math`)
- `stream_version` (0-based, optimistic concurrency)
- `event_type` (e.g. `ProviderStarted`)
- `data` (JSON payload)

### Schema versioning

Every serialized payload includes a schema version field:

```json
{
  "_version": 1,
  "provider_id": "math",
  "mode": "subprocess",
  "tools_count": 3,
  "startup_duration_ms": 50.0
}
```

Backwards compatibility rule:

- Events persisted without `_version` are treated as **v1**.

## Upcasting (schema evolution)

When schemas evolve between releases, older persisted events might not match the current event constructor signature.

MCP Hangar supports **upcasting**: converting an event payload from an older schema version to the current one **at read time**.

### Rules

- Upcasting only happens on **read** (deserialization).
- Upcasters are **pure functions** (no I/O, no time dependence).
- Upcasters must advance **exactly one version step**: `vN -> vN+1`.
- Updating `EVENT_VERSION_MAP` requires providing the full upcaster chain.

### Where versions are defined

Current schema versions live in:

- `mcp_hangar/infrastructure/persistence/event_serializer.py`
  - `EVENT_VERSION_MAP`
  - `get_current_version(event_type)`

### Writing an upcaster

Create an upcaster in `mcp_hangar/infrastructure/persistence/upcasters/`:

```python
from typing import Any

from mcp_hangar.infrastructure.persistence.event_upcaster import IEventUpcaster


class ProviderStartedV1ToV2(IEventUpcaster):
    """Example evolution: add a `tags` field introduced in v2."""

    @property
    def event_type(self) -> str:
        return "ProviderStarted"

    @property
    def from_version(self) -> int:
        return 1

    @property
    def to_version(self) -> int:
        return 2

    def upcast(self, data: dict[str, Any]) -> dict[str, Any]:
        return {**data, "tags": []}
```

### Registering upcasters (composition root)

Upcaster chain is built at startup:

```python
from mcp_hangar.infrastructure.persistence import EventSerializer, SQLiteEventStore
from mcp_hangar.infrastructure.persistence.event_upcaster import UpcasterChain

from mcp_hangar.infrastructure.persistence.upcasters.provider_started import ProviderStartedV1ToV2


chain = UpcasterChain()
chain.register(ProviderStartedV1ToV2())

serializer = EventSerializer(upcaster_chain=chain)
store = SQLiteEventStore(db_path, serializer=serializer)
```

### Forward compatibility (extra payload keys)

Deserializer ignores unknown payload keys when reconstructing event instances. This means newer payloads can contain additional fields without breaking older code paths.

## Troubleshooting

- `UpcastingError: Missing upcaster...` usually means:
  - `EVENT_VERSION_MAP` was bumped, but the full set of upcasters was not registered.
- If you need to rename an event type, treat it as a new type and keep the old one readable via:
  - keeping the old `event_type` registered in `EVENT_TYPE_MAP`, or
  - a custom adapter at the serialization boundary.
