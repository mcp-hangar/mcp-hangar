"""Discovery tools: discover, sources, approve, quarantine.

Uses ApplicationContext for dependency injection (DIP).
"""

from mcp.server.fastmcp import FastMCP

from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input


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
        """Trigger immediate discovery scan (not to be confused with hangar_discovered).

        Scans all enabled sources (Kubernetes, Docker, filesystem, entrypoints)
        for new providers. Use after deploying new providers.

        Note: This TRIGGERS a scan. To LIST pending providers, use hangar_discovered.

        Returns:
            Discovery cycle results, or error if discovery not configured.

        Example:
            hangar_discover()
            # Returns:
            # {
            #   "cycle_id": "...",
            #   "sources_scanned": 3,
            #   "providers_discovered": 2,
            #   "providers_updated": 0,
            #   "providers_removed": 0,
            #   "duration_ms": 142,
            #   "errors": []
            # }
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
        """List providers pending registration (not to be confused with hangar_discover).

        Shows providers found by discovery awaiting approval. Use hangar_approve
        to register them.

        Note: This LISTS pending providers. To TRIGGER a scan, use hangar_discover.

        Returns:
            Pending providers list, or error if discovery not configured.

        Example:
            hangar_discovered()
            # Returns:
            # {
            #   "pending": [
            #     {"name": "new-provider", "source": "kubernetes", "mode": "remote", "discovered_at": "..."}
            #   ]
            # }
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        pending = orchestrator.get_pending_providers()
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
        """List quarantined providers with failure reasons.

        Quarantined providers failed validation or health checks.
        Use hangar_approve to restore them.

        Returns:
            Quarantined providers list, or error if discovery not configured.

        Example:
            hangar_quarantine()
            # Returns:
            # {
            #   "quarantined": [
            #     {"name": "broken-provider", "source": "docker",
            #      "reason": "health_check_failed", "quarantine_time": "..."}
            #   ]
            # }
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        quarantined = orchestrator.get_quarantined()
        return {
            "quarantined": [
                {
                    "name": name,
                    "source": data["provider"]["source_type"],
                    "reason": data["reason"],
                    "quarantine_time": data["quarantine_time"],
                }
                for name, data in quarantined.items()
            ]
        }

    @mcp.tool(name="hangar_approve")
    @mcp_tool_wrapper(
        tool_name="hangar_approve",
        rate_limit_key=lambda provider: f"hangar_approve:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    async def hangar_approve(provider: str) -> dict:
        """Approve a pending or quarantined provider for registration.

        Registers the provider so it becomes available for hangar_call.
        Use hangar_discovered or hangar_quarantine to find providers to approve.

        Args:
            provider: Provider name from pending or quarantine list.

        Returns:
            Approval result, or error if not found/discovery not configured.

        Example:
            hangar_approve("my-new-provider")
            # Returns: {"approved": true, "provider_id": "my-new-provider", "state": "cold"}

            hangar_approve("unknown")
            # Returns: {"approved": false, "error": "provider_not_found"}
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        result = await orchestrator.approve_provider(provider)
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

        Shows status of Kubernetes, Docker, filesystem, and entrypoint scanners.
        Use to diagnose why providers are not being discovered.

        Returns:
            Source status list, or error if discovery not configured.

        Example:
            hangar_sources()
            # Returns:
            # {
            #   "sources": [
            #     {"type": "kubernetes", "enabled": true, "healthy": true, "last_scan": "...", "providers_found": 5},
            #     {"type": "docker", "enabled": true, "healthy": false, "last_error": "socket not found"},
            #     {"type": "filesystem", "enabled": false}
            #   ]
            # }
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        sources = await orchestrator.get_sources_status()
        return {"sources": sources}
