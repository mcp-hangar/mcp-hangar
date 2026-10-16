"""The pin ledger: how the event log learns of the pins in the configuration (#1800).

Pins are declared in the configuration. Nothing recorded that one was created,
re-approved with another digest or removed, so the history of a tool's pin
lived in the file's version control, if anywhere, and in no replica's log.

Each time a configuration is applied -- at boot and on reload -- the pins it
declares are compared with what the server's pin-ledger stream already says,
and the differences are appended: a ``ToolPinRecorded`` for a pin that is new or
whose digest or upstream binding changed (naming the digest it replaced), a
``ToolPinRevoked`` for one the log holds that the file no longer declares. An
unchanged pin appends nothing, so a restart does not repeat the history.

The state is read from the log, not remembered in memory, so a restart and a
second replica reconstruct the same history. Two replicas that start at once can
both append the same line; the state is the last line for a pin, so a duplicate
changes nothing about it.

The approver is ``config``: the configuration file carries no author, and the
file is the authority a pin comes from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.contracts.event_store import NullEventStore
from ...domain.events import ToolPinRecorded, ToolPinRevoked
from ...domain.services.digest_computation import compute_digest_parts
from ...logging_config import get_logger
from ...stream_ids import PIN_LEDGER, SEPARATOR, stream_id_for

logger = get_logger(__name__)

#: (tool, tenant or None) -> (sha256, upstream) as the ledger last recorded it.
LedgerState = dict[tuple[str, str | None], tuple[str, str | None]]


@dataclass(frozen=True)
class Reconciled:
    """What one reconciliation appended."""

    recorded: int
    revoked: int


def ledger_state(events: list[Any]) -> LedgerState:
    """Fold a pin-ledger stream into the pins it currently holds."""
    state: LedgerState = {}
    for event in events:
        if isinstance(event, ToolPinRecorded):
            state[(event.tool_name, event.tenant_id)] = (event.sha256, event.upstream)
        elif isinstance(event, ToolPinRevoked):
            state.pop((event.tool_name, event.tenant_id), None)
    return state


def reconcile_pin_ledger(registry: Any, event_bus: Any) -> Reconciled:
    """Append to each server's pin-ledger stream what the configuration's pins changed (#1800).

    Args:
        registry: The ``ToolProjectionRegistry`` holding the configuration's pins.
        event_bus: Publishes each line to its stream. Its ``event_store`` is read for the state.
    """
    store = getattr(event_bus, "event_store", None)
    if store is None or isinstance(store, NullEventStore):
        # No log to keep the history in; announcing it on every start would only be noise.
        return Reconciled(0, 0)

    declared: dict[str, dict[tuple[str, str | None], Any]] = {}
    for scope, tool, tenant_id, digest in registry.config_pins():
        declared.setdefault(scope, {})[(tool, tenant_id)] = digest
    prefix = f"{PIN_LEDGER}{SEPARATOR}"
    held = {stream[len(prefix) :] for stream in store.list_streams(prefix=prefix)}

    recorded = revoked = 0
    for server in sorted(set(declared) | held):
        state = ledger_state(store.read_stream(stream_id_for(PIN_LEDGER, server)))
        wanted = declared.get(server, {})
        projections = {p.tool: p for p in registry.list_for_server(server)}
        for (tool, tenant_id), digest in sorted(wanted.items(), key=lambda item: (item[0][0], item[0][1] or "")):
            before = state.get((tool, tenant_id))
            if before == (digest.sha256, digest.upstream):
                continue
            structure = description = None
            served = projections.get(tool)
            if served is not None and served.digest.sha256 == digest.sha256:
                structure, description = compute_digest_parts(served.schema)
            event_bus.publish(
                ToolPinRecorded(
                    mcp_server_id=server,
                    tool_name=tool,
                    sha256=digest.sha256,
                    tenant_id=tenant_id,
                    previous_sha256=before[0] if before else None,
                    upstream=digest.upstream,
                    structure_sha256=structure,
                    description_sha256=description,
                )
            )
            recorded += 1
        for (tool, tenant_id), (sha256, _upstream) in sorted(state.items(), key=lambda i: (i[0][0], i[0][1] or "")):
            if (tool, tenant_id) in wanted:
                continue
            event_bus.publish(ToolPinRevoked(mcp_server_id=server, tool_name=tool, sha256=sha256, tenant_id=tenant_id))
            revoked += 1
    if recorded or revoked:
        logger.info("pin_ledger_reconciled", recorded=recorded, revoked=revoked)
    return Reconciled(recorded, revoked)
