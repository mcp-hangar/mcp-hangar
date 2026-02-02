"""Provider interaction tools: hangar_tools, hangar_details, hangar_warm.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.

Note: Tool invocation is handled by hangar_call in batch/.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...application.commands import StartProviderCommand
from ...application.mcp.tooling import mcp_tool_wrapper
from ...application.queries import GetProviderQuery, GetProviderToolsQuery
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
    - hangar_tools: Get tool schemas for a provider
    - hangar_details: Get detailed provider/group info
    - hangar_warm: Pre-start providers to avoid cold start latency

    Note: Tool invocation has been consolidated into hangar_call (batch.py).
    """

    @mcp.tool(name="hangar_tools")
    @mcp_tool_wrapper(
        tool_name="hangar_tools",
        rate_limit_key=lambda provider: f"hangar_tools:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_tools(provider: str) -> dict:
        """Get tool schemas for a provider.

        Returns JSON Schema definitions for all tools. Use this to discover
        available tools and their parameters before calling hangar_call.

        Note: If the provider is COLD, this will start it to discover tools.

        Args:
            provider: Provider ID or Group ID. For groups, selects a healthy member.

        Returns:
            Returns an error if provider or group ID is unknown.

        Example:
            hangar_tools("math")
            # Returns:
            # {
            #   "provider": "math",
            #   "state": "ready",
            #   "predefined": false,
            #   "tools": [
            #     {
            #       "name": "add",
            #       "description": "Add two numbers",
            #       "inputSchema": {
            #         "type": "object",
            #         "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            #         "required": ["a", "b"]
            #       }
            #     }
            #   ]
            # }

            hangar_tools("llm-group")
            # Returns tools from a healthy group member, with "group": true
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return _get_tools_for_group(provider)

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        return _get_tools_for_provider(provider)

    @mcp.tool(name="hangar_details")
    @mcp_tool_wrapper(
        tool_name="hangar_details",
        rate_limit_key=lambda provider: f"hangar_details:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_details(provider: str) -> dict:
        """Get configuration and runtime info for a provider or group.

        Does not start the provider or modify any state.

        Args:
            provider: Provider ID or Group ID.

        Returns:
            For providers: {provider, state, mode, alive, tools, health, idle_time, meta}
            For groups: {group_id, state, strategy, members, healthy_count, total_count}
            Returns an error if unknown.

        Example:
            hangar_details("math")
            # Returns: {"provider": "math", "state": "ready", "mode": "subprocess",
            #           "alive": true, "tools": [...], "health": {...}, "idle_time": 0.0}

            hangar_details("llm-group")
            # Returns: {"group_id": "llm-group", "members": [...], "healthy_count": 2}
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return ctx.get_group(provider).to_status_dict()

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        query = GetProviderQuery(provider_id=provider)
        return ctx.query_bus.execute(query).to_dict()

    @mcp.tool(name="hangar_warm")
    @mcp_tool_wrapper(
        tool_name="hangar_warm",
        rate_limit_key=lambda providers="": "hangar_warm",
        check_rate_limit=check_rate_limit,
        validate=None,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def hangar_warm(providers: str | None = None) -> dict:
        """Pre-start providers to avoid cold start latency on first hangar_call.

        Args:
            providers: Comma-separated provider IDs (e.g., "math,sqlite").
                Omit or pass null to warm ALL configured providers.
                Groups are skipped (use hangar_start for groups).

        Returns:
            - warmed: Provider IDs that were started
            - already_warm: Provider IDs that were already running
            - failed: List of {id, error} for providers that failed
            - summary: Human-readable summary

        Example:
            hangar_warm("math,sqlite")
            # Returns: {"warmed": ["math"], "already_warm": ["sqlite"], "failed": [], "summary": "Warmed 1..."}

            hangar_warm()
            # Warms all configured providers
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
