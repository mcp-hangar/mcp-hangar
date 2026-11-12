"""The claim pass: the manager marks the rows of the servers its file declares (#2171).

A row in `mcp_server_configs` is either a registration, which a restart
restores, or the file's overlay for a server the file declares, which holds its
L7 policy and is not restored once the file drops the server. A row written for
a declared server is born claimed (`_record_fields`). This pass claims the rest:
a server the file took over from the API or discovery, and rows written before
the claim existed. The group and member rows the file declares are claimed the
same way, so a restart on a file that dropped them skips them too.

Who runs it, and when:

- Standalone (no lease keeper): at boot, before anything is served, and after
  each reload. There is no peer to protect, so the write is not fenced.
- Coordinated: only the lease holder writes, under the generation fence -- when
  a tenure begins, after its own reloads, and on a slow timer. A replica without
  the lease writes nothing shared; it still counts the dormant rows.

Every write is monotonic and conditional (`claim_for_file`): a deposed holder,
or a row deleted or rewritten meanwhile, matches nothing. A pass that cannot
reach the database claims nothing, which leaves today's behaviour.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from ...domain.contracts.event_bus import HandlerKind
from ...domain.events import ConfigurationReloaded
from ...logging_config import get_logger
from .coordination import get_lease_keeper, may_manage

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime
    from ...domain.contracts.persistence import StoredGroups

logger = get_logger(__name__)

#: The holder's safety net between tenures and reloads, so a rollout converges.
CLAIM_INTERVAL_S = 60.0

#: Live claimed rows -- servers, groups and members -- this replica's file does
#: not declare, as of its last pass; None before one has read the rows
#: (decision 3: kept, counted).
_dormant: int | None = None
#: Set to ask the timer for a pass now: a tenure began, or the worker stops.
_wake = threading.Event()


def dormant_claimed_rows() -> int | None:
    """How many claimed rows this replica's file does not declare, as of its last pass."""
    return _dormant


def claim_declared_rows() -> int | None:
    """One pass: claim each live, unclaimed row the file declares, if this instance manages.

    Returns:
        How many rows it claimed, or None when there is no durable writer or the
        rows could not be read.
    """
    global _dormant

    from ..config import declared_in_config
    from .cqrs import get_fleet_writer

    writer = get_fleet_writer()
    if writer is None:
        return None
    try:
        rows = writer.read_all()
        group_rows = _group_rows(writer.read_groups())
        _dormant = sum(1 for row in rows if row.claimed_by_file and not declared_in_config(row.mcp_server_id))
        _dormant += sum(1 for *_, claimed, declared in group_rows if claimed and not declared)
    except Exception as e:  # noqa: BLE001 -- fault-barrier: an unclaimed row is today's behaviour, not an outage
        logger.warning(
            "file_claim_pass_failed",
            error=str(e),
            detail="rows of servers the file declares stay unclaimed until the next pass",
        )
        return None
    if not may_manage():
        return 0
    claimed = []
    for row in rows:
        if row.claimed_by_file or not declared_in_config(row.mcp_server_id):
            continue
        try:
            if writer.claim_for_file(row.mcp_server_id):
                claimed.append(row.mcp_server_id)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: one row must not cost the others their claim
            logger.warning("file_claim_row_failed", mcp_server_id=row.mcp_server_id, error=str(e))
    if claimed:
        logger.info("file_claimed_rows", mcp_server_ids=claimed)
    return len(claimed) + _claim_group_rows(writer, group_rows)


def _group_rows(stored: StoredGroups) -> list[tuple[str, str | None, bool, bool]]:
    """Each group row and member row: its group, its member, whether it is claimed, whether the file declares it."""
    from ..config import declared_group_members

    rows: list[tuple[str, str | None, bool, bool]] = []
    for g in stored.groups:
        rows.append((g.group_id, None, g.claimed_by_file, declared_group_members(g.group_id) is not None))
    for m in stored.members:
        named = m.member_id in (declared_group_members(m.group_id) or ())
        rows.append((m.group_id, m.member_id, m.claimed_by_file, named))
    return rows


def _claim_group_rows(writer: Any, rows: list[tuple[str, str | None, bool, bool]]) -> int:
    """Claim each unclaimed group or member row the file declares; how many this claimed."""
    claimed = []
    for group_id, member_id, already, declared in rows:
        if already or not declared:
            continue
        try:
            if writer.claim_group_row_for_file(group_id, member_id):
                claimed.append(group_id if member_id is None else f"{group_id}/{member_id}")
        except Exception as e:  # noqa: BLE001 -- fault-barrier: one row must not cost the others their claim
            logger.warning("file_claim_group_row_failed", group_id=group_id, member_id=member_id, error=str(e))
    if claimed:
        logger.info("file_claimed_group_rows", rows=claimed)
    return len(claimed)


def request_claim_pass(_lease: Any = None) -> None:
    """Ask the timer for a pass now; the keeper's `on_acquired`, which must not wait on the database."""
    _wake.set()


def _after_reload(event: object) -> None:
    if isinstance(event, ConfigurationReloaded):
        claim_declared_rows()


class FileClaimWorker:
    """The lease holder's timer for the claim pass, woken early when a tenure begins."""

    def __init__(self, interval_s: float = CLAIM_INTERVAL_S) -> None:
        self.task = "file_claims"
        self.interval_s = interval_s
        self.running = False
        self.thread = threading.Thread(target=self._loop, daemon=True, name="worker-file-claims")

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        _wake.set()

    def join(self, timeout_s: float | None = None) -> bool:
        if self.thread.ident is None:
            return True
        self.thread.join(timeout_s)
        return not self.thread.is_alive()

    def _loop(self) -> None:
        while self.running:
            _wake.wait(self.interval_s)
            _wake.clear()
            if self.running and may_manage():
                claim_declared_rows()


def install_file_claims(runtime: Runtime) -> FileClaimWorker | None:
    """Run the boot pass, claim after each of this replica's reloads, and build the holder's timer.

    Called after the writer and the lease keeper exist, and before `bootstrap`
    returns, so nothing is served first. On a coordinated gateway the keeper
    has not started, so the boot pass only counts; the tenure's start claims.

    Returns:
        The timer when peers coordinate through a lease, otherwise None.
    """
    claim_declared_rows()
    # EFFECT: it writes shared storage, so only the replica that reloaded runs it.
    runtime.event_bus.subscribe(ConfigurationReloaded, _after_reload, kind=HandlerKind.EFFECT)
    if get_lease_keeper() is None:
        return None
    _wake.clear()
    return FileClaimWorker()
