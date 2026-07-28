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

# INBOUND server identity: the ``serverInfo.name`` Hangar reports to its own
# clients, on every surface that carries one (``initialize`` and the SEP-2575
# ``server/discover`` result). One constant because the two used to disagree --
# the factory said "mcp-hangar" while the shipped ``serve --http`` path said
# "mcp-registry", so a client saw a different server depending on which surface
# it asked (#560). Distinct from ``protocol.HANGAR_CLIENT_INFO``, which is the
# OUTBOUND clientInfo Hangar presents to upstream MCP servers.
HANGAR_SERVER_NAME = "mcp-hangar"


@dataclass(frozen=True)
class HangarFunctions:
    """Container for all control plane function dependencies.

    Core functions are required. Discovery functions are optional
    and will return appropriate errors if not provided.

    Attributes:
        list: Function to list all managed mcp_servers.
        start: Function to start a mcp_server.
        stop: Function to stop a mcp_server.
        invoke: Function to invoke a tool on a mcp_server.
        tools: Function to get tool schemas.
        details: Function to get mcp_server details.
        health: Function to get control plane health.
        discover: Optional async function to trigger discovery.
        discovered: Optional function to list discovered mcp_servers.
        quarantine: Optional function to list quarantined mcp_servers.
        approve: Optional async function to approve a quarantined mcp_server.
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
        relay_tasks_enabled: Kill-switch for the ADR-014 task-relay serving
            surface (**default True — reactivated 2026-07-28**). When True (the
            native-tasks SDK is required) the governed relay is live: the
            ``tasks/*`` handlers are registered and the Tasks extension is
            advertised on ``server/discover``; per ADR-014 D5 the relay itself
            only engages once an upstream actually emits a task, so no
            synchronous flow changes until then.

            It has been on, off, and on again, and the reason matters more than
            the dates. Activated 2026-07-22 (ADR-014 D5/D6). Turned off
            2026-07-28 because the surface advertised a wire it did not serve --
            ``mcp_types`` carries the SEP-1686 shapes, not SEP-2663, so a client
            negotiating 2026-07-28 was handed a reply it could not parse
            (ADR-015). Turned back on the same day once the SEP-2663 wire was
            actually served and verified end to end against a live gateway,
            which is the condition ADR-015 Decision 5 set for reactivating it.

            Set False to disable the surface entirely -- byte-identical to the
            relay-only stance (ADR-008): no ``tasks/*`` handlers registered, no
            extension advertised, upstream task handles rejected.
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
    # ADR-014 task-relay serving surface kill-switch. Reactivated 2026-07-28
    # once the SEP-2663 wire was actually served -- the condition ADR-015
    # Decision 5 set for turning it back on.
    relay_tasks_enabled: bool = True


__all__ = [
    "HANGAR_SERVER_NAME",
    "HangarFunctions",
    "ServerConfig",
]
