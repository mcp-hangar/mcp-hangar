"""Background workers for garbage collection and health checks."""

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from .domain.contracts.mcp_server_runtime import McpServerMapping, McpServerRuntime, normalize_state_to_str
from .infrastructure.event_bus import get_event_bus
from .logging_config import get_logger
from .metrics import observe_health_check, record_error, record_gc_cycle
from .observability.conventions import Health, HealthCheck, McpServer
from .observability.tracing import get_tracer
from .stream_ids import MCP_SERVER

logger = get_logger(__name__)


def _join_thread(thread: threading.Thread | None, timeout_s: float | None) -> bool:
    """Wait up to *timeout_s* for *thread* to end.

    Returns:
        True once it is not running: it ended, or it was never started. False
        if it is still running, or if it is the calling thread, which cannot
        wait for itself.
    """
    if thread is None or thread.ident is None:
        return True
    if thread is threading.current_thread():
        return False
    thread.join(timeout_s)
    return not thread.is_alive()


# Optional watchdog import
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    # Not logged here. This runs at import -- every CLI invocation, `--help` and
    # `pin` included, imports it through `mcp_hangar.server` -- before any
    # command has set up logging, so a line here printed at every level and no
    # control could turn it down (#1236). `ConfigReloadWorker.start()` says it
    # instead, when hot reload asks for watchdog and has to poll.
    WATCHDOG_AVAILABLE = False


class BackgroundWorker:
    """Generic background worker for GC and health checks.

    Expects mcp_server storage that supports `.items()` (dict-like) returning
    `(mcp_server_id, mcp_server)` pairs where `mcp_server` satisfies the `McpServerRuntime`
    contract.

    Works with:
    - McpServer aggregates
    - backward-compatibility wrappers (as long as they implement the contract)
    """

    def __init__(
        self,
        mcp_servers: McpServerMapping,
        interval_s: int = 10,
        task: Literal["gc", "health_check"] = "gc",
        event_bus: Any | None = None,
    ):
        """
        Initialize background worker.

        Args:
            mcp_servers: Dict-like mapping (mcp_server_id -> McpServerRuntime).
            interval_s: Interval between runs in seconds.
            task: Task type - either "gc" (garbage collection) or "health_check".
            event_bus: Optional event bus for publishing events (uses global if not provided).
        """
        self.mcp_servers: McpServerMapping = mcp_servers
        self.interval_s = interval_s
        self.task = task
        self._event_bus = event_bus or get_event_bus()
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"worker-{task}")
        self.running = False
        # Waited on between cycles rather than slept through, so `stop()` ends
        # the thread at once and not up to a whole interval later (#1435).
        self._stopped = threading.Event()
        self._next_check_at: dict[str, float] = {}

    def start(self):
        """Start the background worker thread."""
        if self.running:
            logger.warning("background_worker_already_running", task=self.task)
            return

        self.running = True
        self.thread.start()
        logger.info("background_worker_started", task=self.task, interval_s=self.interval_s)

    def stop(self):
        """Signal the worker thread to stop. Does not block; `join()` waits for it."""
        self.running = False
        self._stopped.set()
        logger.info("background_worker_stopped", task=self.task)

    def join(self, timeout_s: float | None = None) -> bool:
        """Wait up to *timeout_s* for the worker thread to end after `stop()`.

        Returns:
            True once the thread is not running, including when it never started.
        """
        return _join_thread(self.thread, timeout_s)

    def _publish_events(self, mcp_server: McpServerRuntime) -> None:
        """Publish all collected events from a mcp_server.

        McpServerRuntime is expected to support event collection.
        """
        # The runtime port promises `Iterable`, the store wants a list.
        events = list(mcp_server.collect_events())
        if not events:
            return
        try:
            self._event_bus.publish_aggregate_events(MCP_SERVER, mcp_server.mcp_server_id, events)
        except Exception:  # noqa: BLE001 -- fault-barrier: event publishing must not crash background worker
            logger.exception("event_publish_failed")

    def _check_health(self, mcp_server_id: str, mcp_server: McpServerRuntime, now: float) -> None:
        """Check one server that is due, in one `mcp_server.health_check` span (#1296).

        The span covers the check and the publication of what it recorded, so
        a recovery command the saga sends synchronously on that publication is
        its child. Skipped servers and idle ticks never get here, so they emit
        nothing. A check that raises ends the span ERROR and reaches the loop's
        fault barrier as before.
        """
        with get_tracer(__name__).start_as_current_span("mcp_server.health_check") as span:
            span.set_attribute(McpServer.ID, mcp_server_id)
            try:
                hc_start = time.perf_counter()
                is_healthy = mcp_server.health_check()
                hc_duration = time.perf_counter() - hc_start
            except Exception:
                span.set_attribute(HealthCheck.OUTCOME, HealthCheck.ERROR)
                raise

            consecutive = int(getattr(mcp_server.health, "consecutive_failures", 0))
            span.set_attribute(HealthCheck.OUTCOME, HealthCheck.HEALTHY if is_healthy else HealthCheck.UNHEALTHY)
            span.set_attribute(Health.CONSECUTIVE_FAILURES, consecutive)

            observe_health_check(
                mcp_server=mcp_server_id,
                duration=hc_duration,
                healthy=is_healthy,
                is_cold=False,
                consecutive_failures=consecutive,
            )

            if not is_healthy:
                logger.warning("health_check_unhealthy", mcp_server_id=mcp_server_id)

            # Calculate next check interval based on current state
            # Re-read state after health check (it may have changed)
            current_state = normalize_state_to_str(mcp_server.state)
            health_tracker = getattr(mcp_server, "health", None)
            if health_tracker and hasattr(health_tracker, "get_health_check_interval"):
                interval = health_tracker.get_health_check_interval(
                    current_state, normal_interval=float(self.interval_s)
                )
            else:
                interval = float(self.interval_s)

            if interval > 0:
                self._next_check_at[mcp_server_id] = now + interval

            self._publish_events(mcp_server)

    def _loop(self):
        """Main worker loop."""
        while self.running and not self._stopped.wait(self.interval_s):
            start_time = time.perf_counter()
            gc_collected = {"idle": 0, "dead": 0}

            # Get snapshot of mcp_servers to avoid holding mapping lock (if any)
            mcp_servers_snapshot = list(self.mcp_servers.items())

            for mcp_server_id, mcp_server in mcp_servers_snapshot:
                try:
                    if self.task == "gc":
                        # Garbage collection - shutdown idle mcp_servers
                        if mcp_server.maybe_shutdown_idle():
                            logger.info("gc_shutdown", mcp_server_id=mcp_server_id)
                            gc_collected["idle"] += 1
                            # Counted from the McpServerStopped the reap
                            # records, published below, and only there (#1466).

                    elif self.task == "health_check":
                        # State-aware health check scheduling
                        state_str = normalize_state_to_str(mcp_server.state)

                        # Skip mcp_servers that are not started, starting up, or
                        # given up on. A DEAD server stays unprobed until a
                        # deliberate start or a call revives it (#1361); probing
                        # it here counted an "unhealthy" check that never ran.
                        if state_str in ("cold", "initializing", "dead"):
                            continue

                        # Check per-mcp_server timing -- skip if not due yet
                        now = time.time()
                        next_check = self._next_check_at.get(mcp_server_id, 0.0)
                        if now < next_check:
                            continue

                        self._check_health(mcp_server_id, mcp_server, now)
                        continue

                    # Publish any collected events
                    self._publish_events(mcp_server)

                except Exception as e:  # noqa: BLE001 -- fault-barrier: single mcp_server failure must not crash background worker loop
                    record_error("gc", type(e).__name__)
                    logger.exception(
                        "background_task_failed",
                        mcp_server_id=mcp_server_id,
                        task=self.task,
                        error=str(e),
                    )

            # Record GC cycle metrics
            if self.task == "gc":
                duration = time.perf_counter() - start_time
                record_gc_cycle(duration, gc_collected)

            # Clean up stale entries from _next_check_at for removed mcp_servers
            if self.task == "health_check":
                current_ids = {pid for pid, _ in mcp_servers_snapshot}
                stale_ids = set(self._next_check_at) - current_ids
                for stale_id in stale_ids:
                    del self._next_check_at[stale_id]


class ConfigReloadWorker:
    """Background worker for monitoring configuration file changes.

    Supports two modes:
    - Watchdog mode (inotify/fsevents) - preferred, low latency
    - Polling mode (mtime check) - fallback when watchdog unavailable
    """

    # For compatibility with BackgroundWorker interface
    task = "config_reload"

    def __init__(
        self,
        config_path: str | None,
        command_bus: Any,
        interval_s: int = 5,
        use_watchdog: bool = True,
    ):
        """Initialize config reload worker.

        Args:
            config_path: Path to configuration file to monitor.
            command_bus: Command bus for sending ReloadConfigurationCommand.
            interval_s: Polling interval (used only in polling mode).
            use_watchdog: Use watchdog if available, otherwise fall back to polling.
        """
        self.config_path = Path(config_path) if config_path else None
        self.command_bus = command_bus
        self.interval_s = interval_s
        self.use_watchdog = use_watchdog and WATCHDOG_AVAILABLE
        self._watchdog_requested = use_watchdog

        self.thread: threading.Thread | None = None
        self.running = False
        # Waited on between polls, so `stop()` ends the polling thread at once.
        self._stopped = threading.Event()
        self._observer: Any | None = None
        self._last_mtime: float | None = None

        if not self.config_path or not self.config_path.exists():
            logger.warning(
                "config_reload_worker_disabled",
                reason="no_config_path" if not self.config_path else "config_not_found",
                config_path=str(self.config_path),
            )
            self._enabled = False
        else:
            self._enabled = True
            self._last_mtime = self.config_path.stat().st_mtime

    def start(self):
        """Start the config reload worker."""
        if not self._enabled:
            logger.debug("config_reload_worker_not_starting", reason="disabled")
            return

        if self.running:
            logger.warning("config_reload_worker_already_running")
            return

        self.running = True
        self._stopped.clear()

        # Here rather than at import: only a hot reload that wanted watchdog has
        # a reason to hear it is missing, and by now logging is configured.
        if self._watchdog_requested and not WATCHDOG_AVAILABLE:
            logger.debug("watchdog package not installed, config file watching will use polling")

        if self.use_watchdog:
            self._start_watchdog()
        else:
            self._start_polling()

        logger.info(
            "config_reload_worker_started",
            mode="watchdog" if self.use_watchdog else "polling",
            config_path=str(self.config_path),
            interval_s=self.interval_s if not self.use_watchdog else None,
        )

    def stop(self):
        """Stop the config reload worker."""
        if not self.running:
            return

        self.running = False
        self._stopped.set()

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        logger.info("config_reload_worker_stopped")

    def join(self, timeout_s: float | None = None) -> bool:
        """Wait up to *timeout_s* for the polling thread to end after `stop()`.

        The watchdog observer is joined by `stop()` itself.

        Returns:
            True once no polling thread is running, including when none started.
        """
        return _join_thread(self.thread, timeout_s)

    def _start_watchdog(self):
        """Start watchdog-based file monitoring."""
        assert self.config_path is not None
        if not WATCHDOG_AVAILABLE:
            logger.warning("watchdog_not_available_falling_back_to_polling")
            self._start_polling()
            return

        class ConfigFileHandler(FileSystemEventHandler):
            """Watchdog event handler for config file changes."""

            def __init__(self, worker: "ConfigReloadWorker"):
                self.worker = worker
                self._debounce_timer: threading.Timer | None = None
                self._debounce_delay = 1.0  # Wait 1s for multiple rapid changes

            def on_modified(self, event):
                if event.is_directory:
                    return

                # Check if this is our config file
                src_path = str(event.src_path)  # Normalize bytes|str to str
                if (
                    self.worker.config_path is not None
                    and Path(src_path).resolve() == self.worker.config_path.resolve()
                ):
                    self._debounced_reload()

            def _debounced_reload(self):
                """Debounce multiple rapid changes (editors often save multiple times)."""
                if self._debounce_timer:
                    self._debounce_timer.cancel()

                self._debounce_timer = threading.Timer(self._debounce_delay, self.worker._trigger_reload)
                self._debounce_timer.daemon = True
                self._debounce_timer.start()

        try:
            handler = ConfigFileHandler(self)
            self._observer = Observer()
            # Watch the directory containing the config file
            watch_dir = self.config_path.parent
            self._observer.schedule(handler, str(watch_dir), recursive=False)
            self._observer.start()
            logger.info("config_file_watcher_started", watch_dir=str(watch_dir))
        except Exception as e:  # noqa: BLE001 -- fault-barrier: watchdog init failure must not crash config reload worker
            logger.error(
                "watchdog_start_failed_falling_back_to_polling",
                error=str(e),
            )
            self._start_polling()

    def _start_polling(self):
        """Start polling-based file monitoring."""
        self.thread = threading.Thread(target=self._polling_loop, daemon=True, name="config-reload-poller")
        self.thread.start()
        logger.info("config_file_polling_started", interval_s=self.interval_s)

    def _polling_loop(self):
        """Polling loop that checks mtime periodically."""
        assert self.config_path is not None
        assert self._last_mtime is not None
        while self.running and not self._stopped.wait(self.interval_s):
            try:
                if not self.config_path.exists():
                    logger.warning("config_file_disappeared", config_path=str(self.config_path))
                    continue

                current_mtime = self.config_path.stat().st_mtime
                if current_mtime > self._last_mtime:
                    logger.info(
                        "config_file_modified_detected",
                        config_path=str(self.config_path),
                        old_mtime=self._last_mtime,
                        new_mtime=current_mtime,
                    )
                    self._last_mtime = current_mtime
                    self._trigger_reload()

            except Exception as e:  # noqa: BLE001 -- fault-barrier: polling error must not crash config reload worker
                logger.error("config_polling_error", error=str(e))

    def _trigger_reload(self):
        """Trigger configuration reload via command bus."""
        try:
            from .application.commands.commands import ReloadConfigurationCommand

            command = ReloadConfigurationCommand(
                config_path=str(self.config_path),
                graceful=True,
                requested_by="file_watcher",
            )

            logger.info("triggering_config_reload", config_path=str(self.config_path))
            result = self.command_bus.send(command)
            logger.info("config_reload_triggered", result=result)

        except Exception as e:  # noqa: BLE001 -- fault-barrier: reload trigger failure must not crash worker
            logger.error(
                "config_reload_trigger_failed",
                error=str(e),
                error_type=type(e).__name__,
            )


class MetricsSnapshotWorker:
    """Background worker that periodically snapshots per-mcp_server metrics to SQLite.

    Takes a live metric reading every ``interval_s`` seconds and persists the
    resulting :class:`~mcp_hangar.infrastructure.persistence.metrics_history_store.MetricPoint`
    rows via :class:`~mcp_hangar.infrastructure.persistence.metrics_history_store.MetricsHistoryStore`.

    Every ``prune_interval`` snapshots (default: 144 — roughly every 2.4 hours at
    60-second intervals) old rows beyond the retention window are pruned.

    Args:
        interval_s: Seconds between metric snapshots (default: 60).
        prune_interval: How many snapshot cycles between prune runs (default: 144).
    """

    def __init__(
        self,
        interval_s: int = 60,
        prune_interval: int = 144,
        may_manage: Callable[[], bool] | None = None,
    ) -> None:
        self.task = "metrics_snapshot"
        self.interval_s = interval_s
        self._prune_interval = prune_interval
        self._cycle = 0
        self.running = False
        # Unlike GC and health checks, this writes to storage every replica
        # shares. Three snapshot workers on one history table interleave three
        # series into one, and a chart of "calls per minute" then depends on
        # which replica happened to be scheduled. Gated on the lease, and asked
        # per cycle rather than at startup.
        self._may_manage = may_manage or (lambda: True)
        # Waited on between snapshots rather than slept through, so `stop()`
        # ends the thread now and not up to a minute later (#1389).
        self._stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="worker-metrics-snapshot")

    def start(self) -> None:
        """Start the background snapshot worker. A second call does nothing."""
        if self.running:
            logger.warning("metrics_snapshot_worker_already_running")
            return
        self.running = True
        self.thread.start()
        logger.info("metrics_snapshot_worker_started", interval_s=self.interval_s)

    def stop(self) -> None:
        """Signal the worker to stop. Does not block, and its wait ends at once."""
        self.running = False
        self._stopped.set()
        logger.info("metrics_snapshot_worker_stopped")

    def join(self, timeout_s: float | None = None) -> bool:
        """Wait up to *timeout_s* for the worker thread to end after `stop()`.

        Returns:
            True once the thread is not running, including when it never started.
        """
        return _join_thread(self.thread, timeout_s)

    def _loop(self) -> None:
        """Main worker loop."""
        while self.running:
            self._stopped.wait(self.interval_s)
            if not self.running:
                break
            if not self._may_manage():
                continue
            try:
                self._take_snapshot()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: snapshot failure must not crash worker
                logger.error("metrics_snapshot_error", error=str(e))

            self._cycle += 1
            if self._cycle % self._prune_interval == 0:
                try:
                    self._prune()
                except Exception as e:  # noqa: BLE001 -- fault-barrier: prune failure must not crash worker
                    logger.error("metrics_prune_error", error=str(e))

    def _take_snapshot(self) -> None:
        """Collect current metrics and write to the history store."""
        from .infrastructure.persistence.metrics_history_store import MetricPoint, get_metrics_history_store
        from .metrics import get_metrics

        prometheus_text = get_metrics()
        now = time.time()
        per_mcp_server: dict[str, dict[str, float]] = {}

        for line in prometheus_text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            if 'mcp_server="' not in line:
                continue
            try:
                value = float(line.split()[-1])
                parts = line.split('mcp_server="')
                mcp_server_id = parts[1].split('"')[0]
                if not mcp_server_id:
                    continue
            except (ValueError, IndexError):
                continue

            entry = per_mcp_server.setdefault(
                mcp_server_id,
                {
                    "tool_calls_total": 0.0,
                    "tool_call_errors": 0.0,
                    "cold_starts_total": 0.0,
                    "health_checks_total": 0.0,
                    "health_check_failures": 0.0,
                },
            )

            if line.startswith("mcp_hangar_tool_calls_total{"):
                entry["tool_calls_total"] += value
            elif line.startswith("mcp_hangar_tool_call_errors_total{"):
                entry["tool_call_errors"] += value
            elif line.startswith("mcp_hangar_health_checks_total{"):
                entry["health_checks_total"] += value
                if 'result="unhealthy"' in line:
                    entry["health_check_failures"] += value
            elif line.startswith("mcp_hangar_mcp_server_cold_start_seconds_count{"):
                entry["cold_starts_total"] += value

        points: list[MetricPoint] = []
        for mcp_server_id, metrics in per_mcp_server.items():
            for metric_name, metric_value in metrics.items():
                points.append(
                    MetricPoint(
                        mcp_server_id=mcp_server_id,
                        metric_name=metric_name,
                        value=metric_value,
                        recorded_at=now,
                    )
                )

        if points:
            store = get_metrics_history_store()
            store.record_snapshot(points)
            logger.debug("metrics_snapshot_recorded", mcp_servers=len(per_mcp_server), points=len(points))

    def _prune(self) -> None:
        """Prune old metric history rows."""
        from .infrastructure.persistence.metrics_history_store import get_metrics_history_store

        store = get_metrics_history_store()
        deleted = store.prune()
        if deleted:
            logger.info("metrics_history_pruned_by_worker", deleted=deleted)
