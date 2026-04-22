"""Discovery tools: discover, sources, approve, quarantine.

Uses ApplicationContext for dependency injection (DIP).
"""

from mcp.server.fastmcp import FastMCP

from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_mcp_server_id_input


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register discovery tools with MCP server."""

    @mcp.tool(name="hangar_discover")
    @mcp_tool_wrapper(
        tool_name="hangar_discover",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_discover"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    async def hangar_discover() -> dict:
        """Trigger immediate discovery scan across all enabled sources.

        CHOOSE THIS when: you deployed new mcp_servers and want to find them now.
        CHOOSE hangar_discovered when: listing mcp_servers already found, awaiting approval.
        CHOOSE hangar_sources when: checking which discovery sources are working.

        Side effects: Scans all enabled sources. Updates pending mcp_server list.

        Args:
            None

        Returns:
            Success: {
                discovered_count: int,
                registered_count: int,
                updated_count: int,
                deregistered_count: int,
                quarantined_count: int,
                error_count: int,
                duration_ms: float,
                source_results: {<source_type>: int}
            }
            Not configured: {error: str}

        Example:
            hangar_discover()
            # {"discovered_count": 2, "registered_count": 1, "updated_count": 0,
            #  "deregistered_count": 0, "quarantined_count": 0, "error_count": 0,
            #  "duration_ms": 142.5, "source_results": {"kubernetes": 2}}

            hangar_discover()  # when not configured
            # {"error": "Discovery not configured. Enable discovery in config.yaml"}
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        result = await orchestrator.trigger_discovery()
        return result

    @mcp.tool(name="hangar_discovered")
    @mcp_tool_wrapper(
        tool_name="hangar_discovered",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_discovered"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_discovered() -> dict:
        """List mcp_servers pending registration (awaiting approval).

        CHOOSE THIS when: reviewing what mcp_servers were found before approving.
        CHOOSE hangar_discover when: triggering a new scan to find mcp_servers.
        CHOOSE hangar_approve when: ready to register a pending mcp_server.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            Success: {
                pending: [{
                    name: str,
                    source: str,
                    mode: str,
                    discovered_at: str,
                    fingerprint: str
                }]
            }
            Not configured: {error: str}

        Example:
            hangar_discovered()
            # {"pending": [{"name": "new-mcp_server", "source": "kubernetes",
            #   "mode": "remote", "discovered_at": "2024-01-15T10:30:00Z", "fingerprint": "abc123"}]}

            hangar_discovered()  # when no pending mcp_servers
            # {"pending": []}

            hangar_discovered()  # when not configured
            # {"error": "Discovery not configured. Enable discovery in config.yaml"}
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        pending = orchestrator.get_pending_mcp_servers()
        return {
            "pending": [
                {
                    "name": p.name,
                    "source": p.source_type,
                    "mode": p.mode,
                    "discovered_at": p.discovered_at.isoformat(),
                    "fingerprint": p.fingerprint,
                }
                for p in pending
            ]
        }

    @mcp.tool(name="hangar_quarantine")
    @mcp_tool_wrapper(
        tool_name="hangar_quarantine",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_quarantine"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_quarantine() -> dict:
        """List quarantined mcp_servers with failure reasons.

        CHOOSE THIS when: investigating why mcp_servers failed validation or health checks.
        CHOOSE hangar_discovered when: listing mcp_servers that passed validation.
        CHOOSE hangar_approve when: ready to restore a quarantined mcp_server.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            Success: {
                quarantined: [{
                    name: str,
                    source: str,
                    reason: str,
                    quarantine_time: str
                }]
            }
            Not configured: {error: str}

        Example:
            hangar_quarantine()
            # {"quarantined": [{"name": "broken-mcp_server", "source": "docker",
            #   "reason": "health_check_failed", "quarantine_time": "2024-01-15T10:30:00Z"}]}

            hangar_quarantine()  # when no quarantined mcp_servers
            # {"quarantined": []}

            hangar_quarantine()  # when not configured
            # {"error": "Discovery not configured. Enable discovery in config.yaml"}
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        quarantined = orchestrator.get_quarantined()
        return {
            "quarantined": [
                {
                    "name": name,
                    "source": data["mcp_server"]["source_type"],
                    "reason": data["reason"],
                    "quarantine_time": data["quarantine_time"],
                }
                for name, data in quarantined.items()
            ]
        }

    @mcp.tool(name="hangar_approve")
    @mcp_tool_wrapper(
        tool_name="hangar_approve",
        rate_limit_key=lambda mcp_server: f"hangar_approve:{mcp_server}",
        check_rate_limit=check_rate_limit,
        validate=validate_mcp_server_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    async def hangar_approve(mcp_server: str) -> dict:
        """Approve a pending or quarantined mcp_server for registration.

        CHOOSE THIS when: ready to register a mcp_server from pending or quarantine list.
        CHOOSE hangar_discovered when: you need to review pending mcp_servers first.
        CHOOSE hangar_quarantine when: you need to see why a mcp_server was quarantined.

        Side effects: Registers the mcp_server in cold state. Removes from pending/quarantine.

        Args:
            mcp_server: str - McpServer name (from hangar_discovered or hangar_quarantine output)

        Returns:
            Success: {approved: true, mcp_server: str, status: "registered"}
            Not found: {approved: false, mcp_server: str, error: str}
            Not configured: {error: str}

        Example:
            hangar_approve("my-new-mcp_server")
            # {"approved": true, "mcp_server": "my-new-mcp_server", "status": "registered"}

            hangar_approve("unknown")
            # {"approved": false, "mcp_server": "unknown", "error": "McpServer not found in quarantine"}

            hangar_approve("x")  # when not configured
            # {"error": "Discovery not configured. Enable discovery in config.yaml"}
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        result = await orchestrator.approve_mcp_server(mcp_server)
        return result

    @mcp.tool(name="hangar_sources")
    @mcp_tool_wrapper(
        tool_name="hangar_sources",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_sources"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    async def hangar_sources() -> dict:
        """List discovery sources with health status.

        CHOOSE THIS when: diagnosing why mcp_servers are not being discovered.
        CHOOSE hangar_discover when: triggering a scan after fixing source issues.
        CHOOSE hangar_health when: checking overall system health, not just discovery.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            Success: {
                sources: [{
                    source_type: str,
                    mode: str,
                    is_healthy: bool,
                    is_enabled: bool,
                    last_discovery: str | null,
                    mcp_servers_count: int,
                    error_message: str | null
                }]
            }
            Not configured: {error: str}

        Example:
            hangar_sources()
            # {"sources": [
            #   {"source_type": "kubernetes", "mode": "additive", "is_healthy": true,
            #    "is_enabled": true, "last_discovery": "2024-01-15T10:30:00Z",
            #    "mcp_servers_count": 5, "error_message": null},
            #   {"source_type": "docker", "mode": "additive", "is_healthy": false,
            #    "is_enabled": true, "last_discovery": null,
            #    "mcp_servers_count": 0, "error_message": "socket not found"}
            # ]}

            hangar_sources()  # when not configured
            # {"error": "Discovery not configured. Enable discovery in config.yaml"}
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        sources = await orchestrator.get_sources_status()
        return {"sources": sources}
