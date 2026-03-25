"""ResourceMonitorWorker -- background resource usage monitoring. BSL 1.1 licensed.

Orchestrates Docker and Kubernetes resource collectors in a daemon thread.
Polls ALL providers for CPU, memory, and network I/O metrics. Stores samples
in the ResourceStore, updates Prometheus gauges, and emits deviation events
when in ENFORCING mode.

Follows the BackgroundWorker pattern from gc.py / ConnectionLogWorker: daemon
thread, snapshot iteration over providers dict, per-provider fault barrier.

See enterprise/LICENSE.BSL for license terms.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from mcp_hangar.domain.contracts.behavioral import IBehavioralProfiler, IResourceStore
from mcp_hangar.domain.events import BehavioralDeviationDetected
from mcp_hangar.domain.value_objects.behavioral import (
    BehavioralMode,
    DeviationType,
    ResourceSample,
)
from mcp_hangar.domain.value_objects.provider import ProviderMode

logger = structlog.get_logger(__name__)


def _parse_cpu(value: str) -> float:
    """Parse K8s CPU resource value to fractional cores.

    Handles formats: "250m" (millicores), "1000000000n" (nanocores),
    bare integer (cores).

    Args:
        value: CPU resource string from metrics-server.

    Returns:
        CPU usage as fractional cores (e.g. 0.25 for 250m).
    """
    value = value.strip()
    if value.endswith("n"):
        return int(value[:-1]) / 1_000_000_000
    if value.endswith("m"):
        return int(value[:-1]) / 1_000
    return float(value)


def _parse_memory(value: str) -> int:
    """Parse K8s memory resource value to bytes.

    Handles formats: "Ki", "Mi", "Gi", "Ti" suffixes and bare integers.

    Args:
        value: Memory resource string from metrics-server.

    Returns:
        Memory usage in bytes.
    """
    value = value.strip()
    multipliers = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
    }
    for suffix, multiplier in multipliers.items():
        if value.endswith(suffix):
            return int(value[: -len(suffix)]) * multiplier
    return int(value)


class ResourceMonitorWorker:
    """Background worker polling provider containers for resource usage.

    Follows BackgroundWorker pattern: daemon thread, fault barrier per provider,
    snapshot iteration over providers dict.

    Args:
        providers: Dict-like mapping of provider_id -> provider.
        resource_store: IResourceStore for persisting samples and baselines.
        profiler: IBehavioralProfiler for checking provider modes.
        event_bus: Optional event bus for publishing deviation events.
        metrics_publisher: Optional ObservabilityMetrics for Prometheus gauges.
        interval_s: Polling interval in seconds (default 60).
        docker_enabled: Enable Docker container monitoring.
        k8s_enabled: Enable K8s pod monitoring.
        k8s_namespace: K8s namespace for provider pods.
        cpu_deviation_multiplier: Threshold multiplier for CPU spike detection.
        memory_deviation_multiplier: Threshold multiplier for memory spike detection.
        network_io_deviation_multiplier: Threshold multiplier for network I/O spike detection.
        retention_days: Days to retain resource samples before pruning.
        prune_every_n_cycles: Run pruning every N poll cycles.
    """

    task = "resource_monitor"

    def __init__(
        self,
        providers: Any,
        resource_store: IResourceStore,
        profiler: IBehavioralProfiler,
        event_bus: Any = None,
        metrics_publisher: Any = None,
        interval_s: int = 60,
        docker_enabled: bool = True,
        k8s_enabled: bool = True,
        k8s_namespace: str = "default",
        cpu_deviation_multiplier: float = 2.0,
        memory_deviation_multiplier: float = 2.0,
        network_io_deviation_multiplier: float = 2.0,
        retention_days: int = 7,
        prune_every_n_cycles: int = 100,
    ) -> None:
        self.providers = providers
        self._store = resource_store
        self._profiler = profiler
        self._event_bus = event_bus
        self._metrics = metrics_publisher
        self.interval_s = interval_s
        self._docker_enabled = docker_enabled
        self._k8s_enabled = k8s_enabled
        self._k8s_namespace = k8s_namespace
        self._cpu_multiplier = cpu_deviation_multiplier
        self._memory_multiplier = memory_deviation_multiplier
        self._network_io_multiplier = network_io_deviation_multiplier
        self._retention_days = retention_days
        self._prune_every_n_cycles = prune_every_n_cycles

        self.running = False
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="worker-resource-monitor",
        )

        self._docker_client: Any = None
        self._k8s_api: Any = None
        self._k8s_available = True
        self._cycle_count = 0

    def start(self) -> None:
        """Start the background worker thread.

        Idempotent: calling start() on an already-running worker is a no-op.
        """
        if self.running:
            logger.warning("resource_monitor_worker_already_running")
            return

        self.running = True
        self._init_monitors()
        self.thread.start()
        logger.info(
            "resource_monitor_worker_started",
            interval_s=self.interval_s,
            docker_enabled=self._docker_enabled,
            k8s_enabled=self._k8s_enabled,
        )

    def stop(self) -> None:
        """Signal the worker to stop. Thread exits on next cycle."""
        self.running = False
        logger.info("resource_monitor_worker_stopped")

    def _init_monitors(self) -> None:
        """Lazily import and initialize Docker/K8s clients.

        Import failures (e.g. missing docker SDK, missing kubernetes SDK)
        are caught and logged -- the worker continues without that client.
        """
        if self._docker_enabled:
            try:
                import docker

                self._docker_client = docker.from_env()
                logger.info("docker_resource_monitor_initialized")
            except ImportError:
                logger.warning(
                    "docker_resource_monitor_unavailable",
                    reason="import_error",
                )
                self._docker_client = None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "docker_resource_monitor_init_failed",
                    error=str(exc),
                )
                self._docker_client = None

        if self._k8s_enabled:
            try:
                from kubernetes import client, config as k8s_config

                try:
                    k8s_config.load_incluster_config()
                except Exception:  # noqa: BLE001
                    k8s_config.load_kube_config()
                self._k8s_api = client.CustomObjectsApi()
                logger.info("k8s_resource_monitor_initialized")
            except ImportError:
                logger.warning(
                    "k8s_resource_monitor_unavailable",
                    reason="import_error",
                )
                self._k8s_api = None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "k8s_resource_monitor_init_failed",
                    error=str(exc),
                )
                self._k8s_api = None

    def _loop(self) -> None:
        """Main worker loop. Runs until self.running is False."""
        while self.running:
            time.sleep(self.interval_s)
            if not self.running:
                break

            logger.debug("resource_monitor_cycle_start")
            self._cycle_count += 1

            # Snapshot providers to avoid holding dict lock
            providers_snapshot = list(self.providers.items())

            for provider_id, provider in providers_snapshot:
                self._poll_provider(provider_id, provider)

            # Periodic pruning to limit SQLite growth
            if self._cycle_count % self._prune_every_n_cycles == 0:
                try:
                    deleted = self._store.prune(self._retention_days)
                    if deleted > 0:
                        logger.info(
                            "resource_samples_pruned",
                            deleted=deleted,
                            retention_days=self._retention_days,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("resource_prune_failed", error=str(exc))

            logger.debug(
                "resource_monitor_cycle_complete",
                providers_polled=len(providers_snapshot),
                cycle=self._cycle_count,
            )

    def _poll_provider(self, provider_id: str, provider: Any) -> None:
        """Poll a single provider for resource usage.

        Collects CPU, memory, and network I/O from Docker or K8s,
        stores the sample, updates Prometheus gauges, and checks for
        deviations in ENFORCING mode.

        Per-provider fault barrier: exceptions are caught and logged so
        that one failing provider does not prevent polling others.

        Args:
            provider_id: Identifier of the provider.
            provider: Provider object with a mode attribute.
        """
        try:
            provider_mode = getattr(provider, "_mode", getattr(provider, "mode", None))
            sample: ResourceSample | None = None

            if provider_mode == ProviderMode.DOCKER and self._docker_client is not None:
                sample = self._collect_docker_stats(provider_id)
            elif provider_mode == ProviderMode.REMOTE and self._k8s_api is not None and self._k8s_available:
                sample = self._collect_k8s_metrics(provider_id)
            else:
                logger.debug(
                    "resource_monitor_no_collector",
                    provider_id=provider_id,
                    provider_mode=str(provider_mode),
                )
                return

            if sample is None:
                return

            # Store the sample
            self._store.record_sample(sample)

            # Update Prometheus gauges
            if self._metrics is not None:
                self._metrics.update_provider_resources(
                    provider=provider_id,
                    memory_bytes=sample.memory_bytes,
                    cpu_percent=sample.cpu_percent,
                )

            # Check deviations in ENFORCING mode
            mode = self._profiler.get_mode(provider_id)
            if mode == BehavioralMode.ENFORCING:
                baseline = self._store.get_baseline(provider_id)
                if baseline is not None:
                    self._check_resource_deviations(provider_id, sample, baseline)

        except Exception as exc:  # noqa: BLE001 -- fault-barrier: single provider failure must not crash worker
            logger.warning(
                "resource_monitor_provider_poll_failed",
                provider_id=provider_id,
                error=str(exc),
            )

    def _collect_docker_stats(self, provider_id: str) -> ResourceSample | None:
        """Collect resource stats from a Docker container.

        Finds the container by the ``mcp-hangar.provider-id`` label,
        calls ``container.stats(stream=False)``, and calculates CPU %,
        memory, and network I/O.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            ResourceSample or None if container not found or stats unavailable.
        """
        containers = self._docker_client.containers.list(
            filters={
                "label": f"mcp-hangar.provider-id={provider_id}",
                "status": "running",
            },
        )
        if not containers:
            logger.debug("resource_monitor_docker_container_not_found", provider_id=provider_id)
            return None

        container = containers[0]
        stats = container.stats(stream=False)

        # Calculate CPU %
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})
        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get(
            "total_usage", 0
        )
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
        online_cpus = cpu_stats.get("online_cpus", 1)

        cpu_percent = 0.0
        if system_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

        # Memory
        memory_stats = stats.get("memory_stats", {})
        memory_bytes = memory_stats.get("usage", 0)
        memory_limit = memory_stats.get("limit", 0)

        # Network I/O
        networks = stats.get("networks", {})
        rx_bytes = sum(iface.get("rx_bytes", 0) for iface in networks.values())
        tx_bytes = sum(iface.get("tx_bytes", 0) for iface in networks.values())

        return ResourceSample(
            provider_id=provider_id,
            sampled_at=datetime.now(timezone.utc).isoformat(),
            cpu_percent=cpu_percent,
            memory_bytes=memory_bytes,
            memory_limit_bytes=memory_limit,
            network_rx_bytes=rx_bytes,
            network_tx_bytes=tx_bytes,
        )

    def _collect_k8s_metrics(self, provider_id: str) -> ResourceSample | None:
        """Collect resource metrics from K8s metrics-server.

        Uses the metrics.k8s.io API to retrieve pod-level CPU and memory.
        Network I/O is not available from metrics-server (returned as 0).

        Disables K8s monitoring for the remainder of the worker's lifetime
        on a 404 (metrics-server not installed).

        Args:
            provider_id: Identifier of the provider.

        Returns:
            ResourceSample or None if pod not found or metrics unavailable.
        """
        try:
            result = self._k8s_api.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self._k8s_namespace,
                plural="pods",
                label_selector=f"mcp-hangar.provider-id={provider_id}",
            )
        except Exception as exc:  # noqa: BLE001
            # Check for 404 (metrics-server not installed)
            status = getattr(exc, "status", None)
            if status == 404:
                logger.warning(
                    "k8s_metrics_server_not_available",
                    provider_id=provider_id,
                    reason="404_not_found",
                )
                self._k8s_available = False
                return None
            logger.warning(
                "k8s_metrics_query_failed",
                provider_id=provider_id,
                error=str(exc),
            )
            return None

        items = result.get("items", [])
        if not items:
            logger.debug("resource_monitor_k8s_pod_not_found", provider_id=provider_id)
            return None

        # Aggregate across all containers in the first matching pod
        pod = items[0]
        total_cpu = 0.0
        total_memory = 0

        for container_metrics in pod.get("containers", []):
            usage = container_metrics.get("usage", {})
            cpu_raw = usage.get("cpu", "0")
            mem_raw = usage.get("memory", "0")
            total_cpu += _parse_cpu(cpu_raw)
            total_memory += _parse_memory(mem_raw)

        # Convert fractional cores to percentage
        cpu_percent = total_cpu * 100.0

        return ResourceSample(
            provider_id=provider_id,
            sampled_at=datetime.now(timezone.utc).isoformat(),
            cpu_percent=cpu_percent,
            memory_bytes=total_memory,
            memory_limit_bytes=0,  # Not available from metrics-server
            network_rx_bytes=0,  # Not available from metrics-server
            network_tx_bytes=0,  # Not available from metrics-server
        )

    def _check_resource_deviations(
        self,
        provider_id: str,
        sample: ResourceSample,
        baseline: dict[str, Any],
    ) -> None:
        """Check resource usage against baseline and emit deviation events.

        Compares current CPU, memory, and network I/O against the baseline
        mean multiplied by the configured deviation threshold. Emits
        BehavioralDeviationDetected events for any exceeded thresholds.

        Args:
            provider_id: Identifier of the provider.
            sample: Current resource sample.
            baseline: Baseline dict with mean/stddev statistics.
        """
        # CPU spike check
        cpu_mean = baseline.get("cpu_mean", 0.0)
        if cpu_mean > 0 and sample.cpu_percent > cpu_mean * self._cpu_multiplier:
            self._emit_deviation(
                provider_id=provider_id,
                deviation_type=DeviationType.RESOURCE_CPU_SPIKE,
                observed=f"cpu={sample.cpu_percent:.1f}%",
                baseline_expected=f"cpu_mean={cpu_mean:.1f}%, threshold={cpu_mean * self._cpu_multiplier:.1f}%",
            )

        # Memory spike check
        memory_mean = baseline.get("memory_mean", 0.0)
        if memory_mean > 0 and sample.memory_bytes > memory_mean * self._memory_multiplier:
            self._emit_deviation(
                provider_id=provider_id,
                deviation_type=DeviationType.RESOURCE_MEMORY_SPIKE,
                observed=f"memory={sample.memory_bytes} bytes",
                baseline_expected=(
                    f"memory_mean={memory_mean:.0f} bytes, threshold={memory_mean * self._memory_multiplier:.0f} bytes"
                ),
            )

        # Network I/O spike check (combined rx + tx)
        rx_mean = baseline.get("network_rx_mean", 0.0)
        tx_mean = baseline.get("network_tx_mean", 0.0)
        baseline_io = rx_mean + tx_mean
        sample_io = sample.network_rx_bytes + sample.network_tx_bytes
        if baseline_io > 0 and sample_io > baseline_io * self._network_io_multiplier:
            self._emit_deviation(
                provider_id=provider_id,
                deviation_type=DeviationType.RESOURCE_NETWORK_IO_SPIKE,
                observed=f"network_io={sample_io} bytes (rx={sample.network_rx_bytes}, tx={sample.network_tx_bytes})",
                baseline_expected=(
                    f"network_io_mean={baseline_io:.0f} bytes, "
                    f"threshold={baseline_io * self._network_io_multiplier:.0f} bytes"
                ),
            )

    def _emit_deviation(
        self,
        provider_id: str,
        deviation_type: DeviationType,
        observed: str,
        baseline_expected: str,
    ) -> None:
        """Emit a BehavioralDeviationDetected event via the event bus.

        Args:
            provider_id: Provider that deviated.
            deviation_type: Category of deviation.
            observed: Description of observed value.
            baseline_expected: Description of expected baseline.
        """
        event = BehavioralDeviationDetected(
            provider_id=provider_id,
            deviation_type=deviation_type.value,
            observed=observed,
            baseline_expected=baseline_expected,
            severity="high",
        )

        logger.warning(
            "resource_deviation_detected",
            provider_id=provider_id,
            deviation_type=deviation_type.value,
            observed=observed,
            baseline_expected=baseline_expected,
        )

        if self._event_bus is not None:
            try:
                self._event_bus.publish(event)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "resource_deviation_event_publish_failed",
                    provider_id=provider_id,
                    deviation_type=deviation_type.value,
                    error=str(exc),
                )
