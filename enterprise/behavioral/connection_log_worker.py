"""ConnectionLogWorker -- background connection monitoring. BSL 1.1 licensed.

Orchestrates Docker and Kubernetes network monitors in a daemon thread.
Polls providers in LEARNING mode for active TCP connections and feeds
observations to the IBehavioralProfiler pipeline for baseline building.

Follows the BackgroundWorker pattern from gc.py: daemon thread, snapshot
iteration over providers dict, per-provider fault barrier.

See enterprise/LICENSE.BSL for license terms.
"""

import threading
import time
from typing import Any

import structlog

from mcp_hangar.domain.contracts.behavioral import IBehavioralProfiler
from mcp_hangar.domain.contracts.provider_runtime import ProviderMapping
from mcp_hangar.domain.value_objects.behavioral import BehavioralMode
from mcp_hangar.domain.value_objects.provider import ProviderMode

logger = structlog.get_logger(__name__)


class ConnectionLogWorker:
    """Background worker polling provider containers for network connections.

    Follows BackgroundWorker pattern: daemon thread, fault barrier per provider,
    snapshot iteration over providers dict.

    Args:
        providers: Dict-like mapping of provider_id -> provider.
        profiler: Behavioral profiler for recording observations.
        interval_s: Polling interval in seconds (default 30).
        docker_enabled: Enable Docker container monitoring.
        k8s_enabled: Enable K8s pod monitoring.
        k8s_namespace: K8s namespace for provider pods.
    """

    task = "connection_log"

    def __init__(
        self,
        providers: ProviderMapping,
        profiler: IBehavioralProfiler,
        interval_s: int = 30,
        docker_enabled: bool = True,
        k8s_enabled: bool = True,
        k8s_namespace: str = "default",
    ) -> None:
        self.providers = providers
        self._profiler = profiler
        self.interval_s = interval_s
        self._docker_enabled = docker_enabled
        self._k8s_enabled = k8s_enabled
        self._k8s_namespace = k8s_namespace

        self.running = False
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="worker-connection-log",
        )

        self._docker_monitor: Any = None
        self._k8s_monitor: Any = None

    def start(self) -> None:
        """Start the background worker thread.

        Idempotent: calling start() on an already-running worker is a no-op.
        """
        if self.running:
            logger.warning("connection_log_worker_already_running")
            return

        self.running = True
        self._init_monitors()
        self.thread.start()
        logger.info(
            "connection_log_worker_started",
            interval_s=self.interval_s,
            docker_enabled=self._docker_enabled,
            k8s_enabled=self._k8s_enabled,
        )

    def stop(self) -> None:
        """Signal the worker to stop. Thread exits on next cycle."""
        self.running = False
        logger.info("connection_log_worker_stopped")

    def _init_monitors(self) -> None:
        """Lazily import and initialize network monitors.

        Import failures (e.g. missing docker SDK, missing kubernetes SDK)
        are caught and logged -- the worker continues without that monitor.
        """
        if self._docker_enabled:
            try:
                from .docker_network_monitor import DockerNetworkMonitor

                self._docker_monitor = DockerNetworkMonitor()
                logger.info("docker_network_monitor_initialized")
            except ImportError:
                logger.warning(
                    "docker_network_monitor_unavailable",
                    reason="import_error",
                )
                self._docker_monitor = None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "docker_network_monitor_init_failed",
                    error=str(exc),
                )
                self._docker_monitor = None

        if self._k8s_enabled:
            try:
                from .k8s_network_monitor import K8sNetworkMonitor

                self._k8s_monitor = K8sNetworkMonitor()
                logger.info("k8s_network_monitor_initialized")
            except ImportError:
                logger.warning(
                    "k8s_network_monitor_unavailable",
                    reason="import_error",
                )
                self._k8s_monitor = None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "k8s_network_monitor_init_failed",
                    error=str(exc),
                )
                self._k8s_monitor = None

    def _loop(self) -> None:
        """Main worker loop. Runs until self.running is False."""
        while self.running:
            time.sleep(self.interval_s)
            if not self.running:
                break

            logger.debug("connection_log_cycle_start")

            # Snapshot providers to avoid holding dict lock
            providers_snapshot = list(self.providers.items())

            for provider_id, provider in providers_snapshot:
                self._poll_provider(provider_id, provider)

            logger.debug(
                "connection_log_cycle_complete",
                providers_polled=len(providers_snapshot),
            )

    def _poll_provider(self, provider_id: str, provider: Any) -> None:
        """Poll a single provider for network connections.

        Checks profiler mode, selects appropriate monitor, and records
        observations. Only providers in LEARNING mode are polled.

        Per-provider fault barrier: exceptions are caught and logged so
        that one failing provider does not prevent polling others.

        Args:
            provider_id: Identifier of the provider.
            provider: Provider object with a mode attribute.
        """
        try:
            # Only poll providers in LEARNING mode
            mode = self._profiler.get_mode(provider_id)
            if mode != BehavioralMode.LEARNING:
                logger.debug(
                    "connection_log_skipped",
                    provider_id=provider_id,
                    mode=str(mode),
                )
                return

            # Select monitor based on provider mode
            provider_mode = getattr(provider, "_mode", getattr(provider, "mode", None))
            observations = []

            if provider_mode == ProviderMode.DOCKER and self._docker_monitor is not None:
                observations = self._docker_monitor.poll_connections(provider_id)
            elif provider_mode == ProviderMode.REMOTE and self._k8s_monitor is not None:
                observations = self._k8s_monitor.poll_connections(
                    provider_id,
                    namespace=self._k8s_namespace,
                )
            else:
                logger.debug(
                    "connection_log_no_monitor",
                    provider_id=provider_id,
                    provider_mode=str(provider_mode),
                )
                return

            # Record each observation via the profiler
            for obs in observations:
                self._profiler.record_observation(obs)

            if observations:
                logger.info(
                    "connection_log_observations_recorded",
                    provider_id=provider_id,
                    count=len(observations),
                )
        except Exception as exc:  # noqa: BLE001 -- fault-barrier: single provider failure must not crash worker
            logger.warning(
                "connection_log_provider_poll_failed",
                provider_id=provider_id,
                error=str(exc),
            )
