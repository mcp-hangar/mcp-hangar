"""Read the refusals out of the event store, read-only (#1777).

After a first deny a local user had nothing to look at: the refusal exists on
stderr, which a client that spawned the gateway swallows, and in the event
store. This opens that store the way a second process may -- read-only, so the
gateway that holds it open is not disturbed and nothing is written -- and
returns the refusals in it, newest first.

Only the SQLite store is read. The gateway writes it in WAL mode, which a
read-only connection reads alongside the writer.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config_file import default_event_store_path

#: The stored event types that say a call was refused or an approval was not given.
REFUSAL_EVENT_TYPES = (
    "ToolCallRefused",
    "DigestMismatchEvent",
    "ToolApprovalDenied",
    "ToolApprovalExpired",
    "ToolApprovalCancelled",
)


class EventStoreNotReadable(Exception):
    """The event store cannot be read here; the message says why and what to do."""


@dataclass(frozen=True)
class Refusal:
    event_id: str
    event_type: str
    occurred_at: datetime | None
    server: str
    tool: str
    gate: str
    reason: str
    #: Every other field the event carries, for one event shown in full.
    fields: dict[str, Any] = field(default_factory=dict)
    #: The expected and observed digests, for a digest mismatch.
    expected_digest: str | None = None
    observed_digest: str | None = None


def event_store_path(config: dict[str, Any]) -> Path:
    """Where *config* puts the SQLite event store, or raise `EventStoreNotReadable`."""
    persistence = config.get("persistence") or {}
    backend = persistence.get("backend")
    if backend == "sqlite":
        sqlite_block = persistence.get("sqlite") or {}
        override = sqlite_block.get("event_store_path")
        return Path(str(override)) if override else Path(str(sqlite_block.get("data_dir", "data"))) / "events.db"
    if backend:
        raise EventStoreNotReadable(
            f"The event store is in the {backend!r} persistence backend, which `why` does not read. "
            "Query it with that database's tools."
        )

    event_store = config.get("event_store") or {}
    if event_store.get("enabled", True) is False:
        raise EventStoreNotReadable("The event store is disabled in this config, so no refusals were recorded.")
    driver = event_store.get("driver", "sqlite")
    if driver != "sqlite":
        raise EventStoreNotReadable(
            f"The event store driver is {driver!r}: it is not durable, so there is nothing on disk to read."
        )
    return Path(str(event_store.get("path", "data/events.db")))


def default_path() -> Path:
    """Where `init` puts the event store, for a config that names none."""
    return default_event_store_path()


def read_refusals(
    db_path: Path,
    *,
    last: int = 10,
    server: str | None = None,
    event_id: str | None = None,
) -> list[Refusal]:
    """The newest *last* refusals in the store at *db_path*, newest first; or the one with *event_id*."""
    if not db_path.is_file():
        raise EventStoreNotReadable(
            f"No event store at {db_path}. The gateway writes it when it first runs with this config; "
            "pass --db to read another file."
        )
    marks = ",".join("?" for _ in REFUSAL_EVENT_TYPES)
    try:
        # `mode=ro`: no write, no journal, no schema change; WAL is read beside the writer.
        connection = sqlite3.connect(f"file:{quote(str(db_path.absolute()))}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise EventStoreNotReadable(f"Cannot open {db_path} read-only: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT global_position, event_type, data FROM events WHERE event_type IN ({marks}) "
            "ORDER BY global_position DESC",
            REFUSAL_EVENT_TYPES,
        ).fetchall()
    except sqlite3.Error as exc:
        raise EventStoreNotReadable(f"Cannot read {db_path}: {exc}") from exc
    finally:
        connection.close()

    refusals: list[Refusal] = []
    for row in rows:
        refusal = _refusal(str(row["event_type"]), str(row["data"]))
        if refusal is None:
            continue
        if event_id is not None:
            if refusal.event_id == event_id or refusal.event_id.startswith(event_id):
                refusals.append(refusal)
            continue
        if server is not None and refusal.server != server:
            continue
        refusals.append(refusal)
        if len(refusals) >= last:
            break
    return refusals


def _refusal(event_type: str, raw: str) -> Refusal | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    occurred = data.get("occurred_at")
    when = datetime.fromtimestamp(occurred, tz=UTC) if isinstance(occurred, (int, float)) else None
    gate, reason = _gate_and_reason(event_type, data)
    shown = {k: v for k, v in data.items() if k not in {"_version", "schema_version"} and v is not None}
    return Refusal(
        event_id=str(data.get("event_id", "")),
        event_type=event_type,
        occurred_at=when,
        server=str(data.get("mcp_server_id", "")),
        tool=str(data.get("tool_name", "")),
        gate=gate,
        reason=reason,
        fields=shown,
        expected_digest=data.get("expected_digest"),
        observed_digest=data.get("observed_digest"),
    )


def _gate_and_reason(event_type: str, data: dict[str, Any]) -> tuple[str, str]:
    if event_type == "ToolCallRefused":
        if data.get("l7_verdict"):
            return "l7_egress", f"{data['l7_verdict']} ({data.get('l7_rule_kind') or 'policy'})"
        return str(data.get("gate") or ""), str(data.get("gate_reason") or "")
    if event_type == "DigestMismatchEvent":
        return "digest_pin", f"digest differs from the pin; enforcement {data.get('enforcement', '')}"
    if event_type == "ToolApprovalDenied":
        return "approval", f"denied by {data.get('decided_by', '')}: {data.get('reason') or 'no reason given'}"
    if event_type == "ToolApprovalExpired":
        return "approval", "expired with no decision"
    if event_type == "ToolApprovalCancelled":
        return "approval", str(data.get("reason") or "cancelled")
    return "", ""
