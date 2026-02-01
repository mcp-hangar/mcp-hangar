"""Configuration dataclasses for MCP server factory.

Contains HangarFunctions container for control plane dependencies
and ServerConfig for HTTP server settings.
"""

from dataclasses import dataclass

from .protocols import (
    HangarApproveFn,
    HangarDetailsFn,
    HangarDiscoveredFn,
    HangarDiscoverFn,
    HangarHealthFn,
    HangarInvokeFn,
    HangarListFn,
    HangarMetricsFn,
    HangarQuarantineFn,
    HangarSourcesFn,
    HangarStartFn,
    HangarStopFn,
    HangarToolsFn,
)


@dataclass(frozen=True)
class HangarFunctions:
    """Container for all control plane function dependencies.

    Core functions are required. Discovery functions are optional
    and will return appropriate errors if not provided.

    Attributes:
        list: Function to list all managed providers.
        start: Function to start a provider.
        stop: Function to stop a provider.
        invoke: Function to invoke a tool on a provider.
        tools: Function to get tool schemas.
        details: Function to get provider details.
        health: Function to get control plane health.
        discover: Optional async function to trigger discovery.
        discovered: Optional function to list discovered providers.
        quarantine: Optional function to list quarantined providers.
        approve: Optional async function to approve a quarantined provider.
        sources: Optional function to list discovery sources.
        metrics: Optional function to get control plane metrics.
    """

    # Core (required)
    list: HangarListFn
    start: HangarStartFn
    stop: HangarStopFn
    invoke: HangarInvokeFn
    tools: HangarToolsFn
    details: HangarDetailsFn
    health: HangarHealthFn

    # Discovery (optional)
    discover: HangarDiscoverFn | None = None
    discovered: HangarDiscoveredFn | None = None
    quarantine: HangarQuarantineFn | None = None
    approve: HangarApproveFn | None = None
    sources: HangarSourcesFn | None = None
    metrics: HangarMetricsFn | None = None


@dataclass(frozen=True)
class ServerConfig:
    """HTTP server configuration.

    Attributes:
        host: Host to bind to.
        port: Port to bind to.
        streamable_http_path: Path for MCP streamable HTTP endpoint.
        sse_path: Path for SSE endpoint.
        message_path: Path for message endpoint.
        auth_enabled: Whether authentication is enabled (opt-in, default False).
        auth_skip_paths: Paths to skip authentication (health, metrics, etc.).
        trusted_proxies: Set of trusted proxy IPs for X-Forwarded-For.
    """

    host: str = "0.0.0.0"
    port: int = 8000
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    # Auth configuration (opt-in)
    auth_enabled: bool = False
    auth_skip_paths: tuple[str, ...] = ("/health", "/ready", "/_ready", "/metrics")
    trusted_proxies: frozenset[str] = frozenset(["127.0.0.1", "::1"])


__all__ = [
    "HangarFunctions",
    "ServerConfig",
]
