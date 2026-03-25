"""K8s network connection monitor for behavioral profiling -- BSL 1.1 licensed.

Monitors network connections from K8s provider pods using two strategies:
1. Primary: Parse K8s Events for NetworkPolicy audit events
2. Fallback: Exec into provider pods and read /proc/net/tcp

See enterprise/LICENSE.BSL for license terms.
"""

from __future__ import annotations

import re
import time

import structlog

from mcp_hangar.domain.value_objects.behavioral import NetworkObservation

logger = structlog.get_logger(__name__)

try:
    from kubernetes import client as k8s_client, config as k8s_config
    from kubernetes import stream

    K8S_AVAILABLE = True
except ImportError:
    k8s_client = None  # type: ignore[assignment]
    k8s_config = None  # type: ignore[assignment]
    stream = None  # type: ignore[assignment]
    K8S_AVAILABLE = False


# Regex to extract IP:port/protocol from NetworkPolicy event messages.
# Handles patterns like "Denied connection to 10.0.0.5:8080/tcp"
_NP_MESSAGE_PATTERN = re.compile(r"(?:to|from)\s+(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})/(\w+)")


def _parse_proc_net_fallback(content: str) -> list[tuple[str, int, str]]:
    """Fallback parser when proc_net_parser.py is not yet available.

    Attempts to import from the sibling module first (created by plan 43-01).
    Falls back to a minimal inline parser if the module does not exist.

    Args:
        content: Raw text content of /proc/net/tcp.

    Returns:
        List of (host, port, protocol) tuples.
    """
    try:
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        return parse_proc_net_tcp(content)
    except ImportError:
        logger.debug("proc_net_parser_not_available", msg="using inline fallback parser")
        return _inline_parse_proc_net_tcp(content)


def _inline_parse_proc_net_tcp(content: str) -> list[tuple[str, int, str]]:
    """Minimal /proc/net/tcp parser for use when proc_net_parser is unavailable.

    Parses the remote address column (rem_address) from /proc/net/tcp format.
    Each line has hex-encoded IP:port in columns.

    Args:
        content: Raw text content of /proc/net/tcp.

    Returns:
        List of (host, port, protocol) tuples.
    """
    results: list[tuple[str, int, str]] = []
    lines = content.strip().split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        rem_address = parts[2]
        if rem_address == "00000000:0000":
            continue
        try:
            hex_ip, hex_port = rem_address.split(":")
            port = int(hex_port, 16)
            # Convert hex IP (little-endian on x86) to dotted decimal
            ip_int = int(hex_ip, 16)
            ip_str = ".".join(str((ip_int >> (8 * i)) & 0xFF) for i in range(4))
            if ip_str != "0.0.0.0":
                results.append((ip_str, port, "tcp"))
        except (ValueError, IndexError):
            continue
    return results


class K8sNetworkMonitor:
    """Monitors network connections from K8s provider pods.

    Two strategies:
    1. Primary: Watch K8s Events for NetworkPolicy audit events
    2. Fallback: Exec into provider pods and read /proc/net/tcp

    Args:
        namespace: K8s namespace where provider pods run (default: "default").
        use_in_cluster: Whether to use in-cluster config (default: True).

    Raises:
        ImportError: If the kubernetes package is not installed.
    """

    def __init__(self, namespace: str = "default", use_in_cluster: bool = True) -> None:
        if not K8S_AVAILABLE:
            raise ImportError("kubernetes package required for K8s network monitoring")
        if use_in_cluster:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
        else:
            k8s_config.load_kube_config()
        self._core_v1 = k8s_client.CoreV1Api()
        self._namespace = namespace
        self._resource_version: str | None = None
        self._audit_available: bool | None = None  # None = not yet checked

    def poll_connections(self, provider_id: str, namespace: str | None = None) -> list[NetworkObservation]:
        """Poll for network connections from pods of a given provider.

        Tries audit events first (if not known unavailable), then falls back
        to pod exec.

        Args:
            provider_id: The provider identifier to look up pods for.
            namespace: Override namespace (defaults to instance namespace).

        Returns:
            List of NetworkObservation records for observed connections.
        """
        ns = namespace or self._namespace
        raw_connections: list[tuple[str, int, str]] = []

        # Try audit events if not known unavailable
        if self._audit_available is not False:
            raw_connections = self._poll_audit_events(provider_id, ns)

        # Fall back to pod exec if audit yielded nothing
        if not raw_connections:
            raw_connections = self._poll_pod_exec(provider_id, ns)

        now = time.time()
        observations: list[NetworkObservation] = []
        for host, port, protocol in raw_connections:
            try:
                obs = NetworkObservation(
                    timestamp=now,
                    provider_id=provider_id,
                    destination_host=host,
                    destination_port=port,
                    protocol=protocol,
                    direction="outbound",
                )
                observations.append(obs)
            except ValueError as exc:
                logger.warning(
                    "invalid_network_observation",
                    provider_id=provider_id,
                    host=host,
                    port=port,
                    error=str(exc),
                )
        return observations

    def _poll_audit_events(self, provider_id: str, namespace: str) -> list[tuple[str, int, str]]:
        """Parse K8s events with NetworkPolicy reason into connection tuples.

        Args:
            provider_id: Provider whose pods to match events against.
            namespace: K8s namespace to search.

        Returns:
            List of (host, port, protocol) tuples from audit events.
        """
        try:
            events_resp = self._core_v1.list_namespaced_event(namespace)
        except Exception as exc:
            logger.warning(
                "k8s_event_list_failed",
                namespace=namespace,
                error=str(exc),
            )
            self._audit_available = False
            return []

        # Get provider pod names for matching
        provider_pods = self._find_provider_pods(provider_id, namespace)
        pod_names = {p.metadata.name for p in provider_pods}

        results: list[tuple[str, int, str]] = []
        found_np_events = False

        for event in events_resp.items:
            if not event.reason or "NetworkPolicy" not in event.reason:
                continue
            found_np_events = True

            # Check if event involves one of our provider pods
            if event.involved_object and event.involved_object.name in pod_names:
                match = _NP_MESSAGE_PATTERN.search(event.message or "")
                if match:
                    host = match.group(1)
                    port = int(match.group(2))
                    protocol = match.group(3)
                    results.append((host, port, protocol))

        # If we checked and found no NP events at all, mark as unavailable
        if not found_np_events and self._audit_available is None:
            self._audit_available = False
            logger.info(
                "audit_events_unavailable",
                namespace=namespace,
                msg="no NetworkPolicy events found, will use pod exec fallback",
            )
        elif found_np_events:
            self._audit_available = True

        return results

    def _poll_pod_exec(self, provider_id: str, namespace: str) -> list[tuple[str, int, str]]:
        """Exec into provider pods and parse /proc/net/tcp.

        Args:
            provider_id: Provider whose pods to exec into.
            namespace: K8s namespace.

        Returns:
            Aggregated list of (host, port, protocol) tuples from all pods.
        """
        pods = self._find_provider_pods(provider_id, namespace)
        if not pods:
            return []

        all_connections: list[tuple[str, int, str]] = []

        for pod in pods:
            pod_name = pod.metadata.name
            pod_ns = pod.metadata.namespace or namespace
            try:
                output = stream.stream(
                    self._core_v1.connect_get_namespaced_pod_exec,
                    pod_name,
                    pod_ns,
                    command=["cat", "/proc/net/tcp"],
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                )
                connections = _parse_proc_net_fallback(output)
                all_connections.extend(connections)
                logger.debug(
                    "pod_exec_connections",
                    pod=pod_name,
                    count=len(connections),
                )
            except Exception as exc:
                logger.warning(
                    "pod_exec_failed",
                    pod=pod_name,
                    namespace=pod_ns,
                    error=str(exc),
                )
                continue

        return all_connections

    def _find_provider_pods(self, provider_id: str, namespace: str) -> list:
        """Find provider pods by mcp-hangar.provider-id label.

        Args:
            provider_id: The provider ID to match.
            namespace: K8s namespace to search.

        Returns:
            List of Running pod objects matching the label.
        """
        try:
            pod_list = self._core_v1.list_namespaced_pod(
                namespace,
                label_selector=f"mcp-hangar.provider-id={provider_id}",
            )
        except Exception as exc:
            logger.warning(
                "pod_list_failed",
                provider_id=provider_id,
                namespace=namespace,
                error=str(exc),
            )
            return []

        return [pod for pod in pod_list.items if pod.status and pod.status.phase == "Running"]
