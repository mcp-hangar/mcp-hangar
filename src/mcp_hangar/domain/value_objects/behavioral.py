"""Behavioral profiling value objects.

Defines the core data types for the behavioral profiling subsystem:
- BehavioralMode: Operating mode enum (learning, enforcing, disabled)
- NetworkObservation: Immutable record of an observed network connection

These are MIT-licensed domain primitives used by both core and enterprise code.
"""

from dataclasses import dataclass
from enum import Enum


class BehavioralMode(Enum):
    """Operating mode for behavioral profiling of a provider.

    LEARNING: Record observations to build a baseline profile.
    ENFORCING: Compare observations against baseline and flag deviations.
    DISABLED: No profiling active (default for MIT core).
    """

    LEARNING = "learning"
    ENFORCING = "enforcing"
    DISABLED = "disabled"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class NetworkObservation:
    """Immutable record of an observed network connection from a provider.

    Captured by the infrastructure layer (eBPF, conntrack, sidecar proxy)
    and fed into the behavioral profiling pipeline.

    Attributes:
        timestamp: Unix epoch seconds when the connection was observed.
        provider_id: Identifier of the provider that made the connection.
        destination_host: Hostname or IP of the remote endpoint.
        destination_port: TCP/UDP port of the remote endpoint (0-65535).
        protocol: Application or transport protocol (e.g. "tcp", "https").
        direction: Traffic direction ("outbound" or "inbound").
    """

    timestamp: float
    provider_id: str
    destination_host: str
    destination_port: int
    protocol: str
    direction: str

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("NetworkObservation provider_id cannot be empty")
        if not self.destination_host:
            raise ValueError("NetworkObservation destination_host cannot be empty")
        if not (0 <= self.destination_port <= 65535):
            raise ValueError(f"NetworkObservation destination_port must be 0-65535, got {self.destination_port}")
