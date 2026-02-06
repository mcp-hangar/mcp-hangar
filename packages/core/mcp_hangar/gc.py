"""Background workers for garbage collection and health checks."""

from pathlib import Path
import threading
import time
from typing import Any, Literal

from .domain.contracts.provider_runtime import normalize_state_to_str, ProviderMapping, ProviderRuntime
from .infrastructure.event_bus import get_event_bus
from .logging_config import get_logger
from .metrics import observe_health_check, record_error, record_gc_cycle, record_provider_stop

logger = get_logger(__name__)

# Optional watchdog import
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    logger.debug("watchdog package not installed, config file watching will use polling")


class BackgroundWorker:
    """Generic background worker for GC and health checks.

    Expects provider storage that supports `.items()` (dict-like) returning
    `(provider_id, provider)` pairs where `provider` satisfies the `ProviderRuntime`
    contract.

    Works with:
    - Provider aggregates
    - backward-compatibility wrappers (as long as they implement the contract)
    """

    def __init__(
        self,
        providers: ProviderMapping,
        interval_s: int = 10,
        task: Literal["gc", "health_check"] = "gc",
        event_bus: Any | None = None,
    ):
        """
        Initialize background worker.

        Args:
            providers: Dict-like mapping (provider_id -> ProviderRuntime).
            interval_s: Interval between runs in seconds.
            task: Task type - either "gc" (garbage collection) or "health_check".
            event_bus: Optional event bus for publishing events (uses global if not provided).
        """
        self.providers: ProviderMapping = providers
        self.interval_s = interval_s
        self.task = task
        self._event_bus = event_bus or get_event_bus()
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"worker-{task}")
        self.running = False

    def start(self):
        """Start the background worker thread."""
        if self.running:
            logger.warning("background_worker_already_running", task=self.task)
            return

        self.running = True
        self.thread.start()
        logger.info("background_worker_started", task=self.task, interval_s=self.interval_s)

    def stop(self):
        """Stop the background worker thread."""
        self.running = False
        logger.info("background_worker_stopped", task=self.task)

    def _publish_events(self, provider: ProviderRuntime) -> None:
        """Publish all collected events from a provider.

        ProviderRuntime is expected to support event collection.
        """
        for event in provider.collect_events():
            try:
                self._event_bus.publish(event)
            except Exception:
                logger.exception("event_publish_failed")

    def _loop(self):
        """Main worker loop."""
        while self.running:
            time.sleep(self.interval_s)

            start_time = time.perf_counter()
            gc_collected = {"idle": 0, "dead": 0}

            # Get snapshot of providers to avoid holding mapping lock (if any)
            providers_snapshot = list(self.providers.items())

            for provider_id, provider in providers_snapshot:
                try:
                    if self.task == "gc":
                        # Garbage collection - shutdown idle providers
                        if provider.maybe_shutdown_idle():
                            logger.info("gc_shutdown", provider_id=provider_id)
                            gc_collected["idle"] += 1
                            record_provider_stop(provider_id, "idle")

                    elif self.task == "health_check":
                        # Determine whether provider is cold (not started yet)
                        state_str = normalize_state_to_str(provider.state)
                        is_cold = state_str == "cold"

                        # Active health check
                        hc_start = time.perf_counter()
                        is_healthy = provider.health_check()
                        hc_duration = time.perf_counter() - hc_start

                        consecutive = int(getattr(provider.health, "consecutive_failures", 0))

                        observe_health_check(
                            provider=provider_id,
                            duration=hc_duration,
                            healthy=is_healthy,
                            is_cold=is_cold,
                            consecutive_failures=consecutive,
                        )

                        if not is_healthy and not is_cold:
                            logger.warning("health_check_unhealthy", provider_id=provider_id)

                    # Publish any collected events
                    self._publish_events(provider)

                except Exception as e:
                    record_error("gc", type(e).__name__)
                    logger.exception(
                        "background_task_failed",
                        provider_id=provider_id,
                        task=self.task,
                        error=str(e),
                    )

            # Record GC cycle metrics
            if self.task == "gc":
                duration = time.perf_counter() - start_time
                record_gc_cycle(duration, gc_collected)


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

        self.thread: threading.Thread | None = None
        self.running = False
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

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        logger.info("config_reload_worker_stopped")

    def _start_watchdog(self):
        """Start watchdog-based file monitoring."""
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
                if Path(event.src_path).resolve() == self.worker.config_path.resolve():
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
        except Exception as e:
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
        while self.running:
            time.sleep(self.interval_s)

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

            except Exception as e:
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

        except Exception as e:
            logger.error(
                "config_reload_trigger_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
