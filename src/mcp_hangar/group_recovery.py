"""Recovery probe for groups left with no member to select (#1565).

A member rejoins a group's rotation only on a success: a start, a passing
health check or a call. Once every member of a group has left rotation and each
is `cold` (reaped by the GC while it served nothing) or DEAD (the recovery saga
gave up), none of those comes: a call selects only members in rotation, and the
health worker skips `cold` and DEAD servers. The group refused every call until
someone started it by hand.

This worker starts those members again, one pass per interval, with a backoff
per member. A start records `McpServerStarted`, which the group rebalance saga
reports to the group as a success, so rotation and the circuit follow the rules
they follow for any other success; nothing here edits a group.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import bounded_error_type
from .gc import _join_thread
from .infrastructure.event_bus import get_event_bus
from .logging_config import get_logger
from .metrics import record_mcp_server_start
from .stream_ids import MCP_SERVER

logger = get_logger(__name__)

GROUP_RECOVERY_INITIAL_BACKOFF_S = 30.0
"""How long after a failed probe start the member is tried again."""

GROUP_RECOVERY_MAX_BACKOFF_S = 600.0
"""The longest a member waits between probe starts, however many failed."""


@dataclass
class _Backoff:
    """Failed probe starts of one member, and when it may be tried again."""

    failures: int
    next_at: float


class GroupRecoveryWorker:
    """Starts the out-of-rotation members of a group that has nothing to select.

    Replica-local, as the GC and health workers are: it starts this replica's
    own processes and connections, so it is not gated on the lease.

    Args:
        groups: The live groups mapping (group id -> McpServerGroup), read afresh each pass.
        interval_s: Seconds between passes.
        event_bus: Where a started member's events go; the global bus if not given.
        initial_backoff_s: Wait after a member's first failed start; doubled on each further one.
        max_backoff_s: The cap on that wait.
        clock: Monotonic clock, for tests.
    """

    task = "group_recovery"

    def __init__(
        self,
        groups: Mapping[str, Any],
        interval_s: float = 30,
        event_bus: Any | None = None,
        initial_backoff_s: float = GROUP_RECOVERY_INITIAL_BACKOFF_S,
        max_backoff_s: float = GROUP_RECOVERY_MAX_BACKOFF_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._groups = groups
        self.interval_s = interval_s
        self._event_bus = event_bus or get_event_bus()
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max(initial_backoff_s, max_backoff_s)
        self._clock = clock
        # Touched only by the worker's own thread.
        self._backoff: dict[str, _Backoff] = {}
        self.running = False
        self._stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="worker-group-recovery")

    def start(self) -> None:
        """Start the worker thread. A second call does nothing."""
        if self.running:
            logger.warning("background_worker_already_running", task=self.task)
            return
        self.running = True
        self.thread.start()
        logger.info("background_worker_started", task=self.task, interval_s=self.interval_s)

    def stop(self) -> None:
        """Signal the worker to stop. Does not block; `join()` waits for it."""
        self.running = False
        self._stopped.set()
        logger.info("background_worker_stopped", task=self.task)

    def join(self, timeout_s: float | None = None) -> bool:
        """Wait up to *timeout_s* for the worker thread to end after `stop()`."""
        return _join_thread(self.thread, timeout_s)

    def _loop(self) -> None:
        while self.running and not self._stopped.wait(self.interval_s):
            try:
                self.probe_once()
            except Exception:  # noqa: BLE001 -- fault-barrier: one failed pass must not end the worker
                logger.exception("group_recovery_pass_failed")

    def probe_once(self) -> None:
        """One pass: start every due candidate of every group that has nothing to select."""
        now = self._clock()
        members: set[str] = set()
        in_rotation: set[str] = set()
        probed: set[str] = set()
        for group_id, group in list(self._groups.items()):
            try:
                for member in group.members:
                    members.add(member.id)
                    if member.in_rotation:
                        in_rotation.add(member.id)
                candidates = group.recovery_candidates()
            except Exception:  # noqa: BLE001 -- fault-barrier: one group must not stop the others' recovery
                logger.exception("group_recovery_snapshot_failed", group_id=group_id)
                continue
            for server in candidates:
                if not self.running:  # stopped mid-pass: start nothing more after shutdown
                    return
                server_id = str(server.mcp_server_id)
                if server_id in probed:  # a member of two stuck groups is started once
                    continue
                probed.add(server_id)
                self._probe(str(group_id), server_id, server, now)
        self._forget(members, in_rotation)

    def _probe(self, group_id: str, server_id: str, server: Any, now: float) -> None:
        """Start one member unless it is backing off. No lock is held here."""
        entry = self._backoff.get(server_id)
        if entry is not None and now < entry.next_at:
            logger.debug(
                "group_recovery_probe_deferred",
                group_id=group_id,
                mcp_server_id=server_id,
                retry_in_s=round(entry.next_at - now, 1),
            )
            return

        failures = entry.failures if entry is not None else 0
        logger.info(
            "group_recovery_probe_started",
            group_id=group_id,
            mcp_server_id=server_id,
            state=server.state_snapshot.value,
            attempt=failures + 1,
        )
        error_type: str | None = None
        try:
            # A deliberate start: a member Hangar gave up on is started as
            # `hangar_start` would start it, without waiting out its backoff.
            # This worker's own backoff is what spaces the attempts.
            server.ensure_ready()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: a failed start is an outcome, logged below
            error_type = bounded_error_type(type(e).__qualname__)
        finally:
            # McpServerStarted reaches the group through the saga: the only way
            # this worker puts a member back in rotation.
            self._publish_events(server)

        record_mcp_server_start(server_id, success=error_type is None)
        if error_type is None:
            self._backoff.pop(server_id, None)
            logger.info("group_recovery_probe_succeeded", group_id=group_id, mcp_server_id=server_id)
            return

        failures += 1
        delay = min(self._initial_backoff_s * (2 ** (failures - 1)), self._max_backoff_s)
        self._backoff[server_id] = _Backoff(failures=failures, next_at=now + delay)
        logger.warning(
            "group_recovery_probe_failed",
            group_id=group_id,
            mcp_server_id=server_id,
            error_type=error_type,
            failures=failures,
            retry_in_s=delay,
        )

    def _forget(self, members: set[str], in_rotation: set[str]) -> None:
        """Drop the backoff of a member back in rotation by any path, or in no group any more.

        Kept while the member is out of rotation, whatever its state: a member
        started but not yet back (a `healthy_threshold` above 1), or degraded
        by a failed probe start, would otherwise be started again at once the
        next time it is `cold` or DEAD.
        """
        for server_id in list(self._backoff):
            if server_id not in members or server_id in in_rotation:
                del self._backoff[server_id]

    def _publish_events(self, server: Any) -> None:
        events = list(server.collect_events())
        if not events:
            return
        try:
            self._event_bus.publish_aggregate_events(MCP_SERVER, server.mcp_server_id, events)
        except Exception:  # noqa: BLE001 -- fault-barrier: event publishing must not crash the worker
            logger.exception("event_publish_failed")
