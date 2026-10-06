"""Re-list the servers that carry a digest pin, on an interval (#1693).

The digest gate compares a pin with the catalogue the gateway holds, and that
catalogue was refreshed only by a start and by an upstream's own
``tools/list_changed``. An upstream that changed a pinned tool without saying
so was served under the old pin until the gateway restarted.

This worker re-lists every READY server that a pin covers -- a pin on the
server itself, or on a group it is a member of, for all tenants or for one --
once per ``tool_projection.pin_recheck_interval_s``. A changed listing replaces
the catalogue and its projection through the same seam ``tools/list_changed``
uses, so the digest gate refuses a drifted tool from the next call on, with
the refusal, audit record and events it always had. On top of the per-call
``DigestMismatchEvent``, the worker publishes one when it first sees a pin
drift, so the drift is on record before anyone calls the tool.

Detection window: an unannounced change is served for at most one interval
plus the time the pass takes to list the pinned servers (5 s per listing at
most). A listing that fails keeps the previous catalogue, so a server that
cannot be listed is not re-checked; its health checks list it too, and
failing them degrades it. A server that is not READY -- ``cold``, starting,
degraded or DEAD -- is not listed and never started: its catalogue is
re-listed when it starts.

Per replica, as the GC and health workers are: each replica checks the
catalogue it serves from, so it is not gated on the lease.
"""

import threading
from collections.abc import Callable, Mapping
from typing import Any

from .application.read_models.tool_projection import get_tool_projection_registry
from .domain.services.digest_validator import DigestValidator
from .domain.value_objects.tool_digest import DigestPolicy, DigestUnknownPolicy
from .errors import bounded_error_type
from .gc import _join_thread
from .infrastructure.event_bus import get_event_bus
from .logging_config import get_logger

logger = get_logger(__name__)

PIN_RECHECK_DEFAULT_INTERVAL_S = 60
"""Seconds between passes when ``tool_projection.pin_recheck_interval_s`` is not set."""

PIN_RECHECK_MIN_INTERVAL_S = 5
PIN_RECHECK_MAX_INTERVAL_S = 3600
"""The range a non-zero interval must fall in. 0 turns the re-check off."""

#: The correlation id of a mismatch the worker found, rather than a call.
RECHECK_CORRELATION_ID = "pin_recheck"


def pin_recheck_interval_s(config: Mapping[str, Any] | None) -> int:
    """Read ``tool_projection.pin_recheck_interval_s``; refuse a value outside its range.

    Refused rather than defaulted: a typo that quietly became 0 would turn the
    re-check off and leave drift unseen until a restart, which is the defect
    the setting exists to close.

    Raises:
        ValueError: The value is not an integer, or not 0 and outside
            ``PIN_RECHECK_MIN_INTERVAL_S``..``PIN_RECHECK_MAX_INTERVAL_S``.
    """
    section = (config or {}).get("tool_projection")
    if not isinstance(section, Mapping) or "pin_recheck_interval_s" not in section:
        return PIN_RECHECK_DEFAULT_INTERVAL_S
    value = section["pin_recheck_interval_s"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"tool_projection.pin_recheck_interval_s must be an integer, got {value!r}")
    if value != 0 and not PIN_RECHECK_MIN_INTERVAL_S <= value <= PIN_RECHECK_MAX_INTERVAL_S:
        raise ValueError(
            f"tool_projection.pin_recheck_interval_s must be 0 (off) or between {PIN_RECHECK_MIN_INTERVAL_S} "
            f"and {PIN_RECHECK_MAX_INTERVAL_S}, got {value}"
        )
    return value


class PinRecheckWorker:
    """Re-lists the READY servers a digest pin covers, once per interval.

    Args:
        servers: The server repository (``get(id)``), read afresh each pass.
        groups: The live groups mapping (group id -> McpServerGroup), read afresh each pass.
        interval_s: Seconds between passes.
        event_bus: Where a newly seen mismatch is published; the global bus if not given.
        registry: The projection registry; the current global one, looked up each pass, if not given.
    """

    task = "pin_recheck"

    def __init__(
        self,
        servers: Any,
        groups: Mapping[str, Any],
        interval_s: float = PIN_RECHECK_DEFAULT_INTERVAL_S,
        event_bus: Any | None = None,
        registry: Callable[[], Any] = get_tool_projection_registry,
    ) -> None:
        self._servers = servers
        self._groups = groups
        self.interval_s = interval_s
        self._event_bus = event_bus or get_event_bus()
        self._registry = registry
        # Mismatches already published, so a drift is reported once and not
        # every pass. Touched only by the worker's own thread.
        self._reported: set[tuple[str, str, str, str | None, str]] = set()
        self.running = False
        self._stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="worker-pin-recheck")

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
                self.recheck_once()
            except Exception:  # noqa: BLE001 -- fault-barrier: one failed pass must not end the worker
                logger.exception("pin_recheck_pass_failed")

    def recheck_once(self) -> None:
        """One pass: re-list every READY server a pin covers, then report any new mismatch."""
        registry = self._registry()
        # Every id is collected before any I/O, and no group lock is held for it.
        covered: dict[str, list[tuple[str, str, str | None, Any]]] = {}
        for scope, tool, tenant_id, digest in registry.config_pins():
            group = self._groups.get(scope)
            try:
                server_ids = [str(member.id) for member in group.members] if group is not None else [scope]
            except Exception:  # noqa: BLE001 -- fault-barrier: one group must not stop the others' re-check
                logger.exception("pin_recheck_group_snapshot_failed", group_id=scope)
                continue
            for server_id in server_ids:
                covered.setdefault(server_id, []).append((scope, tool, tenant_id, digest))

        # Each server behind its own barrier, the report included: the order is
        # the same every pass, so one that always raised would otherwise stop
        # the re-check of every server after it for good.
        for server_id, pins in covered.items():
            server = self._servers.get(server_id)
            if server is None:
                continue
            try:
                # READY only: a server that is not is neither listed nor started.
                server.recheck_catalogue()
                self._report(registry, server_id, pins)
            except Exception as exc:  # noqa: BLE001 -- fault-barrier: one server must not stop the others' re-check
                logger.warning(
                    "pin_recheck_failed",
                    mcp_server_id=server_id,
                    error_type=bounded_error_type(type(exc).__qualname__),
                )

    def _report(self, registry: Any, server_id: str, pins: list[tuple[str, str, str | None, Any]]) -> None:
        """Publish the mismatch event once for each pin of *server_id* that its projection no longer matches."""
        projections = {projection.tool: projection for projection in registry.list_for_server(server_id)}
        for scope, tool, tenant_id, pin in pins:
            projection = projections.get(tool)
            if projection is None:
                continue
            key = (server_id, scope, tool, tenant_id, projection.digest.sha256)
            if projection.digest.sha256 == pin.sha256:
                self._reported = {k for k in self._reported if k[:4] != key[:4]}
                continue
            if key in self._reported:
                continue
            self._reported.add(key)
            enforcement = registry.digest_enforcement(scope)
            result = DigestValidator(
                DigestPolicy(enforcement=enforcement, unknown=DigestUnknownPolicy.BLOCK, allowlist=frozenset({pin}))
            ).validate_tool(projection.schema, server_id, RECHECK_CORRELATION_ID, tenant_id=tenant_id)
            logger.warning(
                "tool_digest_pin_drift_detected",
                mcp_server_id=server_id,
                scope_id=scope,
                tool=tool,
                tenant_id=tenant_id,
                enforcement=enforcement.value,
            )
            if result.event is not None:
                self._event_bus.publish(result.event)
