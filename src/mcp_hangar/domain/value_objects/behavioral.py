"""Behavioral profiling value objects.

Defines the core data types for the behavioral profiling subsystem:
- BehavioralMode: Operating mode enum (learning, enforcing, disabled)
- DeviationType: Classification of behavioral deviations detected
- NetworkObservation: Immutable record of an observed network connection

These are MIT-licensed domain primitives used by both core and enterprise code.
"""

from dataclasses import dataclass
from enum import Enum


class BehavioralMode(Enum):
    """Operating mode for behavioral profiling of a mcp_server.

    LEARNING: Record observations to build a baseline profile.
    ENFORCING: Compare observations against baseline and flag deviations.
    DISABLED: No profiling active (default for MIT core).
    """

    LEARNING = "learning"
    ENFORCING = "enforcing"
    DISABLED = "disabled"

    def __str__(self) -> str:
        return self.value


class DeviationType(Enum):
    """Classification of behavioral deviations detected during ENFORCING mode.

    NEW_DESTINATION: McpServer contacted a (host, port) pair not in the baseline.
    FREQUENCY_ANOMALY: A destination is contacted at a rate significantly above
        the mcp_server's average destination rate.
    PROTOCOL_DRIFT: A known (host, port) pair was contacted using a different
        protocol than recorded in the baseline.
    SCHEMA_DRIFT: Placeholder for future tool schema drift detection.
    RESOURCE_CPU_SPIKE: McpServer CPU usage significantly exceeds baseline mean.
    RESOURCE_MEMORY_SPIKE: McpServer memory usage significantly exceeds baseline mean.
    RESOURCE_NETWORK_IO_SPIKE: McpServer network I/O significantly exceeds baseline mean.
    """

    NEW_DESTINATION = "new_destination"
    FREQUENCY_ANOMALY = "frequency_anomaly"
    PROTOCOL_DRIFT = "protocol_drift"
    SCHEMA_DRIFT = "schema_drift"
    RESOURCE_CPU_SPIKE = "resource_cpu_spike"
    RESOURCE_MEMORY_SPIKE = "resource_memory_spike"
    RESOURCE_NETWORK_IO_SPIKE = "resource_network_io_spike"

    def __str__(self) -> str:
        return self.value


class SchemaChangeType(Enum):
    """Classification of tool schema changes between mcp_server restarts.

    ADDED: A new tool appeared that was not present in the previous snapshot.
    REMOVED: A tool present in the previous snapshot is no longer advertised.
    MODIFIED: A tool's input parameter schema changed (different hash).
    """

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, init=False)
class NetworkObservation:
    """Immutable record of an observed network connection from a mcp_server.

    Captured by the infrastructure layer (eBPF, conntrack, sidecar proxy)
    and fed into the behavioral profiling pipeline.

    Attributes:
        timestamp: Unix epoch seconds when the connection was observed.
        mcp_server_id: Identifier of the mcp_server that made the connection.
        destination_host: Hostname or IP of the remote endpoint.
        destination_port: TCP/UDP port of the remote endpoint (0-65535).
        protocol: Application or transport protocol (e.g. "tcp", "https").
        direction: Traffic direction ("outbound" or "inbound").
    """

    timestamp: float
    mcp_server_id: str
    destination_host: str
    destination_port: int
    protocol: str
    direction: str

    def __init__(
        self,
        timestamp: float,
        mcp_server_id: str | None = None,
        destination_host: str = "",
        destination_port: int = 0,
        protocol: str = "",
        direction: str = "",
        **kwargs: object,
    ) -> None:
        legacy_id = kwargs.pop("provider_id", None)
        resolved_id = mcp_server_id if mcp_server_id is not None else (legacy_id if isinstance(legacy_id, str) else None)
        if resolved_id is None:
            raise TypeError("Missing required argument: mcp_server_id")
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "mcp_server_id", resolved_id)
        object.__setattr__(self, "destination_host", destination_host)
        object.__setattr__(self, "destination_port", destination_port)
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "direction", direction)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.mcp_server_id:
            raise ValueError("NetworkObservation mcp_server_id cannot be empty")
        if not self.destination_host:
            raise ValueError("NetworkObservation destination_host cannot be empty")
        if not (0 <= self.destination_port <= 65535):
            raise ValueError(f"NetworkObservation destination_port must be 0-65535, got {self.destination_port}")

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id


@dataclass(frozen=True)
class ResourceSample:
    """Immutable record of a resource usage sample from a mcp_server container.

    Captured by the ResourceMonitorWorker from Docker stats or K8s metrics
    and fed into the resource profiling pipeline.

    Attributes:
        mcp_server_id: Identifier of the mcp_server.
        sampled_at: ISO 8601 timestamp when the sample was taken.
        cpu_percent: CPU usage percentage (0-100+, can exceed 100 on multi-core).
        memory_bytes: Current memory usage in bytes.
        memory_limit_bytes: Memory limit of the container in bytes.
        network_rx_bytes: Cumulative network bytes received.
        network_tx_bytes: Cumulative network bytes transmitted.
    """

    mcp_server_id: str
    sampled_at: str
    cpu_percent: float
    memory_bytes: int
    memory_limit_bytes: int
    network_rx_bytes: int
    network_tx_bytes: int
