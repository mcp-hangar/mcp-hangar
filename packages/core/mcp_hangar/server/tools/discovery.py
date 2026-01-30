"""Discovery tools: discover, sources, approve, quarantine.

Uses ApplicationContext for dependency injection (DIP).
"""

from mcp.server.fastmcp import FastMCP

from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register discovery tools with MCP server."""

    @mcp.tool(name="registry_discover")
    @mcp_tool_wrapper(
        tool_name="registry_discover",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_discover"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    async def registry_discover() -> dict:
        """
        Trigger immediate discovery cycle across all configured sources.

        Returns:
            Dictionary with discovery statistics
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        result = await orchestrator.trigger_discovery()
        return result

    @mcp.tool(name="registry_discovered")
    @mcp_tool_wrapper(
        tool_name="registry_discovered",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_discovered"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def registry_discovered() -> dict:
        """
        List all discovered providers pending registration.

        Returns:
            Dictionary with 'pending' key containing list of pending providers
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

    @mcp.tool(name="registry_quarantine")
    @mcp_tool_wrapper(
        tool_name="registry_quarantine",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_quarantine"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def registry_quarantine() -> dict:
        """
        List quarantined providers with failure reasons.

        Returns:
            Dictionary with 'quarantined' key containing list of quarantined providers
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

    @mcp.tool(name="registry_approve")
    @mcp_tool_wrapper(
        tool_name="registry_approve",
        rate_limit_key=lambda provider: f"registry_approve:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    async def registry_approve(provider: str) -> dict:
        """
        Approve a quarantined provider for registration.

        Args:
            provider: Name of the quarantined provider to approve

        Returns:
            Dictionary with approval result
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        result = await orchestrator.approve_provider(provider)
        return result

    @mcp.tool(name="registry_sources")
    @mcp_tool_wrapper(
        tool_name="registry_sources",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_sources"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    async def registry_sources() -> dict:
        """
        List configured discovery sources with health status.

        Returns:
            Dictionary with 'sources' key containing list of source status
        """
        orchestrator = get_context().discovery_orchestrator
        if orchestrator is None:
            return {"error": "Discovery not configured. Enable discovery in config.yaml"}

        sources = await orchestrator.get_sources_status()
        return {"sources": sources}
