"""Background workers for garbage collection and health checks."""

import threading
import time
from typing import Any, Literal

from .domain.contracts.provider_runtime import ProviderMapping, ProviderRuntime, normalize_state_to_str
from .infrastructure.event_bus import get_event_bus
from .logging_config import get_logger
from .metrics import observe_health_check, record_error, record_gc_cycle, record_provider_stop

logger = get_logger(__name__)


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
