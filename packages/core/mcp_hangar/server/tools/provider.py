"""Provider interaction tools: tools, details, warm.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.

Note: Tool invocation (registry_invoke, registry_invoke_ex, registry_invoke_stream)
has been consolidated into hangar_call in batch.py.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...application.commands import StartProviderCommand
from ...application.mcp.tooling import mcp_tool_wrapper
from ...infrastructure.query_bus import GetProviderQuery, GetProviderToolsQuery
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input

# =============================================================================
# Helper Functions
# =============================================================================


def _get_tools_for_group(provider: str) -> dict[str, Any]:
    """Get tools for a provider group."""
    ctx = get_context()
    group = ctx.get_group(provider)
    selected = group.select_member()

    if not selected:
        raise ValueError(f"no_healthy_members_in_group: {provider}")

    ctx.command_bus.send(StartProviderCommand(provider_id=selected.provider_id))
    query = GetProviderToolsQuery(provider_id=selected.provider_id)
    tools = ctx.query_bus.execute(query)

    return {
        "provider": provider,
        "group": True,
        "tools": [t.to_dict() for t in tools],
    }


def _get_tools_for_provider(provider: str) -> dict[str, Any]:
    """Get tools for a single provider."""
    ctx = get_context()
    provider_obj = ctx.get_provider(provider)

    # If provider has predefined tools, return them without starting
    if provider_obj.has_tools:
        tools = provider_obj.tools.list_tools()
        return {
            "provider": provider,
            "state": provider_obj.state.value,
            "predefined": provider_obj.tools_predefined,
            "tools": [t.to_dict() for t in tools],
        }

    # Start provider and discover tools
    ctx.command_bus.send(StartProviderCommand(provider_id=provider))
    query = GetProviderToolsQuery(provider_id=provider)
    tools = ctx.query_bus.execute(query)

    return {
        "provider": provider,
        "state": provider_obj.state.value,
        "predefined": False,
        "tools": [t.to_dict() for t in tools],
    }


# =============================================================================
# Tool Registration
# =============================================================================


def register_provider_tools(mcp: FastMCP) -> None:
    """Register provider interaction tools with MCP server.

    Registers:
    - registry_tools: Get tool schemas for a provider
    - registry_details: Get detailed provider/group info
    - registry_warm: Pre-start providers to avoid cold start latency

    Note: Tool invocation has been consolidated into hangar_call (batch.py).
    """

    @mcp.tool(name="registry_tools")
    @mcp_tool_wrapper(
        tool_name="registry_tools",
        rate_limit_key=lambda provider: f"registry_tools:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_tools(provider: str) -> dict:
        """
        Get detailed tool schemas for a provider.

        This is a QUERY operation with potential side-effect (starting provider).

        Args:
            provider: Provider ID

        Returns:
            Dictionary with provider ID and list of tool schemas

        Raises:
            ValueError: If provider ID is unknown or invalid
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return _get_tools_for_group(provider)

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        return _get_tools_for_provider(provider)

    @mcp.tool(name="registry_details")
    @mcp_tool_wrapper(
        tool_name="registry_details",
        rate_limit_key=lambda provider: f"registry_details:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_details(provider: str) -> dict:
        """
        Get detailed information about a provider or group.

        This is a QUERY operation - no side effects.

        Args:
            provider: Provider ID or Group ID

        Returns:
            Dictionary with full provider/group details

        Raises:
            ValueError: If provider ID is unknown or invalid
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return ctx.get_group(provider).to_status_dict()

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        query = GetProviderQuery(provider_id=provider)
        return ctx.query_bus.execute(query).to_dict()

    @mcp.tool(name="registry_warm")
    @mcp_tool_wrapper(
        tool_name="registry_warm",
        rate_limit_key=lambda providers="": "registry_warm",
        check_rate_limit=check_rate_limit,
        validate=None,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def registry_warm(providers: str | None = None) -> dict:
        """
        Pre-start (warm up) providers to avoid cold start latency.

        Starts the specified providers in advance so they're ready
        when you need them. This eliminates cold start delays.

        Args:
            providers: Comma-separated list of provider IDs to warm up.
                      If empty, warms all providers.

        Returns:
            Dictionary with status for each provider:
            - warmed: List of successfully started providers
            - already_warm: List of providers that were already running
            - failed: List of providers that failed to start

        Example:
            registry_warm("math,sqlite")  # Warm specific providers
            registry_warm()               # Warm all providers
        """
        ctx = get_context()

        # Parse provider list
        if providers:
            provider_ids = [p.strip() for p in providers.split(",") if p.strip()]
        else:
            provider_ids = list(ctx.repository.get_all().keys())

        warmed = []
        already_warm = []
        failed = []

        for provider_id in provider_ids:
            # Skip groups
            if ctx.group_exists(provider_id):
                continue

            if not ctx.provider_exists(provider_id):
                failed.append({"id": provider_id, "error": "Provider not found"})
                continue

            try:
                provider_obj = ctx.get_provider(provider_id)
                if provider_obj and provider_obj.state.value == "ready":
                    already_warm.append(provider_id)
                else:
                    command = StartProviderCommand(provider_id=provider_id)
                    ctx.command_bus.send(command)
                    warmed.append(provider_id)
            except Exception as e:
                failed.append({"id": provider_id, "error": str(e)[:100]})

        return {
            "warmed": warmed,
            "already_warm": already_warm,
            "failed": failed,
            "summary": f"Warmed {len(warmed)} providers, {len(already_warm)} already warm, {len(failed)} failed",
        }
