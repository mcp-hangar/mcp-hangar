"""Rebuilding the runtime withdrawal overlay a restart would otherwise drop.

The projection next door (`WithdrawalProjection`) carries a withdrawal to the
replicas that are running when it is made. This is the other half: a replica
that starts later -- a rolling restart, a scale-up, the pod that was rescheduled
-- learns about withdrawals decided before its tail cursor existed.

Reading is cheap because the events have their own stream per server
(`tool_withdrawal:<id>`) rather than sharing the server's history with every
invocation it has ever served: the whole read is withdrawals and restores.

Order matters and matches the fleet restore next to it: the tailer takes the log
head first, then this folds the log up to that point. An event landing in
between is delivered by the tail, which re-applies it -- the projection is
idempotent, so applying it twice is applying it once.
"""

from __future__ import annotations

from typing import Any

from ...application.event_handlers.withdrawal_projection import WithdrawalProjection
from ...application.read_models.tool_projection import ToolProjectionRegistry, get_tool_projection_registry
from ...logging_config import get_logger
from ...stream_ids import SEPARATOR, TOOL_WITHDRAWAL

logger = get_logger(__name__)


def restore_runtime_withdrawals(runtime: Any, registry: ToolProjectionRegistry | None = None) -> int:
    """Fold every recorded withdrawal back into this replica's overlay.

    Args:
        runtime: The assembled runtime.
        registry: The overlay to rebuild. Defaults to the process singleton,
            which is the one bootstrap means; a caller passes its own only to
            rebuild an overlay that is not this process's.

    Returns:
        How many withdrawal events were applied.
    """
    store = getattr(getattr(runtime, "event_bus", None), "event_store", None)
    if store is None or not getattr(store, "can_replay", False):
        # Nothing was written down, so there is nothing to read back. Not a
        # warning: a single-replica deployment with no event store is a
        # supported configuration, and its withdrawals live for as long as the
        # process does -- which is what it asked for.
        return 0

    projection = WithdrawalProjection(registry if registry is not None else get_tool_projection_registry())
    applied = 0
    try:
        for stream_id in store.list_streams(prefix=f"{TOOL_WITHDRAWAL}{SEPARATOR}"):
            for event in store.read_stream(stream_id):
                projection.handle(event)
                applied += 1
    except Exception as e:  # noqa: BLE001 -- fault-barrier: see below
        # Loud, and not fatal in the same way `restore_persisted_fleet` is not:
        # refusing to boot on an unreadable log would turn a storage hiccup into
        # an outage. But say plainly what is missing, because the gap here is
        # enforcement: tools an operator withdrew may be servable on this
        # replica until the row is readable and it restarts.
        logger.error(
            "withdrawal_restore_failed",
            error=str(e),
            detail="runtime withdrawals could not be replayed; previously withdrawn tools may be served here",
        )
        return applied

    if applied:
        logger.info("withdrawals_restored", count=applied)
    return applied
