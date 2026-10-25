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

#: The stored event types that say a call was refused or an approval was not given.
REFUSAL_EVENT_TYPES = (
    "ToolCallRefused",
    "DigestMismatchEvent",
    "ToolApprovalDenied",
    "ToolApprovalExpired",
    "ToolApprovalCancelled",
)

#: A failed invocation is a refusal when the transport refused the upstream's
#: key (ADR-030 §5): only rows with this code are read as one.
PEER_KEY_REFUSAL = "upstream_peer_key_mismatch"


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
    #: The TLS key a refused upstream presented, for a peer-key refusal (ADR-030 §6).
    observed_peer_spki: str | None = None


@dataclass(frozen=True)
class PinEntry:
    """One line of a tool's pin history (#1800)."""

    event: str  # "recorded" or "revoked"
    occurred_at: datetime | None
    server: str
    tool: str
    tenant_id: str | None
    sha256: str
    previous_sha256: str | None
    upstream: str | None
    structure_sha256: str | None
    description_sha256: str | None
    approved_by: str


def read_pin_history(db_path: Path, tool: str, *, server: str | None = None) -> list[PinEntry]:
    """Every pin recorded or revoked for *tool*, oldest first, read from the pin-ledger streams (#1800).

    From the log alone: no configuration and no running gateway is consulted, so
    the answer is the same after a restart and on another replica reading the
    same store. Read-only, like :func:`read_refusals`.
    """
    rows = _read_rows(db_path, ("ToolPinRecorded", "ToolPinRevoked"), order="ASC")
    history: list[PinEntry] = []
    for event_type, raw in rows:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("tool_name") != tool:
            continue
        if server is not None and data.get("mcp_server_id") != server:
            continue
        occurred = data.get("occurred_at")
        recorded = event_type == "ToolPinRecorded"
        history.append(
            PinEntry(
                event="recorded" if recorded else "revoked",
                occurred_at=datetime.fromtimestamp(occurred, tz=UTC) if isinstance(occurred, (int, float)) else None,
                server=str(data.get("mcp_server_id", "")),
                tool=tool,
                tenant_id=data.get("tenant_id"),
                sha256=str(data.get("sha256", "")),
                previous_sha256=data.get("previous_sha256"),
                upstream=data.get("upstream"),
                structure_sha256=data.get("structure_sha256"),
                description_sha256=data.get("description_sha256"),
                approved_by=str(data.get("approved_by" if recorded else "revoked_by", "")),
            )
        )
    return history


def _read_rows(db_path: Path, event_types: tuple[str, ...], *, order: str) -> list[tuple[str, str]]:
    """``(event_type, data)`` of the stored events of *event_types*, read-only, in position order."""
    if not db_path.is_file():
        raise EventStoreNotReadable(
            f"No event store at {db_path}. The gateway writes it when it first runs with this config"
            + ("" if db_path.is_absolute() else ", from the directory it runs in, as this is read from the current one")
            + "; pass --db to read another file."
        )
    marks = ",".join("?" for _ in event_types)
    try:
        # `mode=ro`: no write, no journal, no schema change; WAL is read beside the writer.
        connection = sqlite3.connect(f"file:{quote(str(db_path.absolute()))}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise EventStoreNotReadable(f"Cannot open {db_path} read-only: {exc}") from exc
    try:
        return [
            (str(r[0]), str(r[1]))
            for r in connection.execute(
                f"SELECT event_type, data FROM events WHERE event_type IN ({marks}) ORDER BY global_position {order}",
                event_types,
            ).fetchall()
        ]
    except sqlite3.Error as exc:
        raise EventStoreNotReadable(f"Cannot read {db_path}: {exc}") from exc
    finally:
        connection.close()


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


def read_refusals(
    db_path: Path,
    *,
    last: int = 10,
    server: str | None = None,
    event_id: str | None = None,
) -> list[Refusal]:
    """The newest *last* refusals in the store at *db_path*, newest first; or the one with *event_id*."""
    # A warm call refused for its upstream's key is recorded as a failed
    # invocation, not as a ToolCallRefused; it is read here as what it is, and
    # never re-labelled as a refusal event.
    rows = _read_rows(db_path, (*REFUSAL_EVENT_TYPES, "ToolInvocationFailed"), order="DESC")

    refusals: list[Refusal] = []
    for event_type, raw in rows:
        refusal = _refusal(event_type, raw)
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
    if event_type == "ToolInvocationFailed" and data.get("failure_code") != PEER_KEY_REFUSAL:
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
        observed_peer_spki=data.get("observed_peer_spki"),
    )


def _gate_and_reason(event_type: str, data: dict[str, Any]) -> tuple[str, str]:
    if event_type == "ToolCallRefused":
        if data.get("l7_verdict"):
            return "l7_egress", f"{data['l7_verdict']} ({data.get('l7_rule_kind') or 'policy'})"
        return str(data.get("gate") or ""), str(data.get("gate_reason") or "")
    if event_type == "ToolInvocationFailed":
        return "upstream_tls", PEER_KEY_REFUSAL
    if event_type == "DigestMismatchEvent":
        return "digest_pin", f"digest differs from the pin; enforcement {data.get('enforcement', '')}"
    if event_type == "ToolApprovalDenied":
        return "approval", f"denied by {data.get('decided_by', '')}: {data.get('reason') or 'no reason given'}"
    if event_type == "ToolApprovalExpired":
        return "approval", "expired with no decision"
    if event_type == "ToolApprovalCancelled":
        return "approval", str(data.get("reason") or "cancelled")
    return "", ""
