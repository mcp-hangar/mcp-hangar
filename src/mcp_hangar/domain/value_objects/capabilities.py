"""Capability declaration value objects for MCP providers.

Providers declare what they need at configuration time.
Hangar verifies at runtime that they do not exceed those declarations.
Deviation triggers alerts and optionally hard-blocks the provider.

This module defines the domain model for the capability declaration schema
(PRODUCT_ARCHITECTURE.md Phase 1, P0).

Example configuration:

    providers:
      my_provider:
        mode: docker
        image: my-mcp-server:latest
        capabilities:
          network:
            egress:
              - host: api.openai.com
                port: 443
                protocol: https
              - host: "*.internal.corp"
                port: 443
                protocol: https
            dns_allowed: true
          filesystem:
            read_paths:
              - /data/knowledge-base
            write_paths: []
            temp_allowed: true
          environment:
            required:
              - OPENAI_API_KEY
            optional:
              - LOG_LEVEL
          tools:
            max_count: 50
            schema_drift_alert: true
          resources:
            max_memory_mb: 512
            max_cpu_percent: 50
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EgressRule:
    """Single allowed egress destination for a provider.

    Attributes:
        host: Hostname or glob pattern (e.g. "api.openai.com" or "*.internal.corp").
        port: TCP port number. Use 0 to allow any port.
        protocol: Application protocol hint ("https", "http", "grpc", "any").
    """

    host: str
    port: int = 443
    protocol: str = "https"

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("EgressRule host cannot be empty")
        if not (0 <= self.port <= 65535):
            raise ValueError(f"EgressRule port must be 0-65535, got {self.port}")
        allowed_protocols = {"https", "http", "grpc", "tcp", "any"}
        if self.protocol not in allowed_protocols:
            raise ValueError(f"EgressRule protocol must be one of {allowed_protocols}")


@dataclass(frozen=True)
class NetworkCapabilities:
    """Network access requirements for a provider.

    Attributes:
        egress: Explicit list of allowed outbound destinations.
            Empty list means deny-all egress.
        dns_allowed: Whether the provider may make DNS queries beyond
            the declared egress destinations.
        loopback_allowed: Whether the provider may connect to localhost/127.0.0.1.
    """

    egress: tuple[EgressRule, ...] = field(default_factory=tuple)
    dns_allowed: bool = True
    loopback_allowed: bool = False

    def __post_init__(self) -> None:
        # Ensure egress is a tuple even if constructed with a list
        object.__setattr__(self, "egress", tuple(self.egress))

    @classmethod
    def deny_all(cls) -> "NetworkCapabilities":
        """No egress allowed — most restrictive preset."""
        return cls(egress=(), dns_allowed=False, loopback_allowed=False)

    @classmethod
    def allow_all(cls) -> "NetworkCapabilities":
        """Unrestricted egress — least restrictive preset. Use only for development."""
        return cls(
            egress=(EgressRule(host="*", port=0, protocol="any"),),
            dns_allowed=True,
            loopback_allowed=True,
        )


@dataclass(frozen=True)
class FilesystemCapabilities:
    """Filesystem access requirements for a provider.

    Attributes:
        read_paths: Explicit allowed read paths.
        write_paths: Explicit allowed write paths.
        temp_allowed: Whether writes to /tmp are permitted.
    """

    read_paths: tuple[str, ...] = field(default_factory=tuple)
    write_paths: tuple[str, ...] = field(default_factory=tuple)
    temp_allowed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "read_paths", tuple(self.read_paths))
        object.__setattr__(self, "write_paths", tuple(self.write_paths))

    @classmethod
    def read_only(cls, *paths: str) -> "FilesystemCapabilities":
        """Read-only access to the given paths."""
        return cls(read_paths=paths, write_paths=(), temp_allowed=False)

    @classmethod
    def none(cls) -> "FilesystemCapabilities":
        """No filesystem access beyond the container root."""
        return cls(read_paths=(), write_paths=(), temp_allowed=False)


@dataclass(frozen=True)
class EnvironmentCapabilities:
    """Environment variable requirements for a provider.

    Attributes:
        required: Variables the provider must have to function.
        optional: Variables the provider may use if present.
    """

    required: tuple[str, ...] = field(default_factory=tuple)
    optional: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "required", tuple(self.required))
        object.__setattr__(self, "optional", tuple(self.optional))

    def all_declared(self) -> frozenset[str]:
        """All declared environment variables."""
        return frozenset(self.required) | frozenset(self.optional)


@dataclass(frozen=True)
class ToolCapabilities:
    """Expected tool schema constraints for a provider.

    Attributes:
        max_count: Maximum number of tools the provider may advertise.
            Use 0 for unlimited.
        schema_drift_alert: Whether to alert when the tool schema changes
            between restarts.
    """

    max_count: int = 0
    schema_drift_alert: bool = True

    def __post_init__(self) -> None:
        if self.max_count < 0:
            raise ValueError("ToolCapabilities.max_count cannot be negative")


@dataclass(frozen=True)
class ResourceCapabilities:
    """Resource consumption limits for a provider.

    These are soft limits used for behavioral profiling and alerting.
    Hard enforcement is delegated to the container runtime (cgroups/K8s).

    Attributes:
        max_memory_mb: Maximum expected memory usage in MiB. 0 = unlimited.
        max_cpu_percent: Maximum expected CPU utilization percent. 0.0 = unlimited.
    """

    max_memory_mb: int = 0
    max_cpu_percent: float = 0.0

    def __post_init__(self) -> None:
        if self.max_memory_mb < 0:
            raise ValueError("ResourceCapabilities.max_memory_mb cannot be negative")
        if self.max_cpu_percent < 0.0:
            raise ValueError("ResourceCapabilities.max_cpu_percent cannot be negative")


@dataclass(frozen=True)
class ProviderCapabilities:
    """Full capability declaration for a provider.

    This is the machine-readable contract that a provider declares at
    configuration time. Hangar enforces these declarations at runtime:

    - Network: generates NetworkPolicy (K8s) or iptables rules (Docker)
    - Filesystem: configures read-only root + explicit volume mounts
    - Environment: validates required env vars are present before start
    - Tools: alerts on schema drift or count violations
    - Resources: sets cgroup/K8s resource limits and alerts on excess

    Deviation between declared and observed behavior triggers:
    - CapabilityViolationDetected domain event
    - Provider quarantine (optional, configurable)
    - Audit log entry with full context

    Attributes:
        network: Network egress requirements.
        filesystem: Filesystem mount requirements.
        environment: Required/optional environment variables.
        tools: Tool schema constraints.
        resources: Resource consumption expectations.
        enforcement_mode: How violations are handled.
            "alert" -- log and emit event, allow the provider to continue.
            "block" -- deny the violating action and emit event.
            "quarantine" -- block the provider from serving new requests.
    """

    network: NetworkCapabilities = field(default_factory=NetworkCapabilities)
    filesystem: FilesystemCapabilities = field(default_factory=FilesystemCapabilities)
    environment: EnvironmentCapabilities = field(default_factory=EnvironmentCapabilities)
    tools: ToolCapabilities = field(default_factory=ToolCapabilities)
    resources: ResourceCapabilities = field(default_factory=ResourceCapabilities)
    enforcement_mode: str = "alert"

    def __post_init__(self) -> None:
        allowed_modes = {"alert", "block", "quarantine"}
        if self.enforcement_mode not in allowed_modes:
            raise ValueError(
                f"ProviderCapabilities.enforcement_mode must be one of {allowed_modes}, got {self.enforcement_mode!r}"
            )

    @classmethod
    def default(cls) -> "ProviderCapabilities":
        """Default capabilities: alert-mode, no egress restrictions declared."""
        return cls(enforcement_mode="alert")

    @classmethod
    def strict(cls) -> "ProviderCapabilities":
        """Strict preset: deny-all egress, no filesystem writes, block on violation."""
        return cls(
            network=NetworkCapabilities.deny_all(),
            filesystem=FilesystemCapabilities.none(),
            enforcement_mode="block",
        )

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> ProviderCapabilities:
        """Create ProviderCapabilities from a YAML configuration dict.

        Args:
            config: Parsed capabilities dict from YAML, or None for defaults.

        Returns:
            ProviderCapabilities with parsed sub-objects.

        Raises:
            ValueError: If any sub-object validation fails (e.g. empty host,
                invalid port, unknown enforcement_mode).
        """
        if not config:
            return cls()  # Default: unconstrained, alert mode

        # Parse network capabilities
        network_data = config.get("network", {})
        egress_rules = tuple(
            EgressRule(
                host=rule.get("host", ""),
                port=rule.get("port", 443),
                protocol=rule.get("protocol", "https"),
            )
            for rule in network_data.get("egress", [])
        )
        network = NetworkCapabilities(
            egress=egress_rules,
            dns_allowed=network_data.get("dns_allowed", True),
            loopback_allowed=network_data.get("loopback_allowed", False),
        )

        # Parse filesystem capabilities
        fs_data = config.get("filesystem", {})
        filesystem = FilesystemCapabilities(
            read_paths=tuple(fs_data.get("read_paths", [])),
            write_paths=tuple(fs_data.get("write_paths", [])),
            temp_allowed=fs_data.get("temp_allowed", True),
        )

        # Parse environment capabilities
        env_data = config.get("environment", {})
        environment = EnvironmentCapabilities(
            required=tuple(env_data.get("required", [])),
            optional=tuple(env_data.get("optional", [])),
        )

        # Parse tool capabilities
        tools_data = config.get("tools", {})
        tools = ToolCapabilities(
            max_count=tools_data.get("max_count", 0),
            schema_drift_alert=tools_data.get("schema_drift_alert", True),
        )

        # Parse resource capabilities
        res_data = config.get("resources", {})
        resources = ResourceCapabilities(
            max_memory_mb=res_data.get("max_memory_mb", 0),
            max_cpu_percent=res_data.get("max_cpu_percent", 0.0),
        )

        return cls(
            network=network,
            filesystem=filesystem,
            environment=environment,
            tools=tools,
            resources=resources,
            enforcement_mode=config.get("enforcement_mode", "alert"),
        )

    def has_egress_rules(self) -> bool:
        """Whether explicit egress rules have been declared."""
        return len(self.network.egress) > 0
