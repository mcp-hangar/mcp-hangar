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
            surface (**default False — deactivated 2026-07-28**, see below).
            When True (native-tasks SDK required) the governed task relay is
            live: the ``tasks/*`` handlers are registered and the ``tasks``
            capability is advertised at INITIALIZE; per D5 the relay itself only
            engages once an upstream actually emits a task, so no synchronous
            flow changes until then.

            It was activated (default True) on 2026-07-22 and turned back off on
            2026-07-28 because the surface advertises a wire it does not serve.
            ``mcp_types`` still carries the SEP-1686 task shapes -- nested
            ``CreateTaskResult{task}``, ``ttl``, ``pollInterval``,
            ``tasks/result``, no ``resultType`` -- while a client negotiating
            2026-07-28 expects the SEP-2663 shapes (flat ``CreateTaskResult``
            with ``resultType: "task"``, ``ttlMs``, ``pollIntervalMs``, no
            ``tasks/result``). Advertising ``tasks`` hands that client a reply it
            cannot parse, and it has no way to detect the mismatch first. Those
            types never evolve in place -- SEP-2663 lands as a separate extension
            defining its own models (python-sdk#3005) -- so the surface stays off
            by default until Hangar serves the SEP-2663 wire.
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
    # ADR-014 task-relay serving surface kill-switch. Activated 2026-07-22,
    # deactivated 2026-07-28: the advertised `tasks` capability serves the
    # SEP-1686 shapes still carried by `mcp_types`, not the SEP-2663 shapes a
    # 2026-07-28 client expects. Opt-in until the wire is realigned.
    relay_tasks_enabled: bool = False


__all__ = [
    "HANGAR_SERVER_NAME",
    "HangarFunctions",
    "ServerConfig",
]
