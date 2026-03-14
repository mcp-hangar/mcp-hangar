"""Provider interaction tools: hangar_tools, hangar_details, hangar_warm.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.

Note: Tool invocation is handled by hangar_call in batch/.
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ...application.commands import StartProviderCommand
from ...application.mcp.tooling import mcp_tool_wrapper
from ...application.queries import GetProviderQuery, GetProviderToolsQuery
from ...domain.services import get_tool_access_resolver
from ...metrics import TOOLS_FILTERED_TOTAL
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input

logger = logging.getLogger(__name__)

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

    # Apply tool access filtering for group context
    resolver = get_tool_access_resolver()
    filtered_tools = resolver.filter_tools(
        provider_id=selected.provider_id,
        tools=tools,
        group_id=provider,
        member_id=selected.provider_id,
    )

    if len(filtered_tools) < len(tools):
        filtered_count = len(tools) - len(filtered_tools)
        TOOLS_FILTERED_TOTAL.set(filtered_count, provider=provider)
        logger.debug(
            "tools_filtered_by_policy: provider_id=%s, group_id=%s, total=%d, visible=%d, filtered=%d",
            selected.provider_id,
            provider,
            len(tools),
            len(filtered_tools),
            filtered_count,
        )

    return {
        "provider": provider,
        "group": True,
        "tools": [t.to_dict() for t in filtered_tools],
    }


def _get_tools_for_provider(provider: str) -> dict[str, Any]:
    """Get tools for a single provider."""
    ctx = get_context()
    provider_obj = ctx.get_provider(provider)
    resolver = get_tool_access_resolver()

    # If provider has predefined tools, return them without starting
    if provider_obj.has_tools:
        tools = provider_obj.tools.list_tools()
        # Apply tool access filtering
        filtered_tools = resolver.filter_tools(provider_id=provider, tools=tools)

        if len(filtered_tools) < len(tools):
            filtered_count = len(tools) - len(filtered_tools)
            TOOLS_FILTERED_TOTAL.set(filtered_count, provider=provider)
            logger.debug(
                "tools_filtered_by_policy: provider_id=%s, total=%d, visible=%d, filtered=%d",
                provider,
                len(tools),
                len(filtered_tools),
                filtered_count,
            )

        return {
            "provider": provider,
            "state": provider_obj.state.value,
            "predefined": provider_obj.tools_predefined,
            "tools": [t.to_dict() for t in filtered_tools],
        }

    # Start provider and discover tools
    ctx.command_bus.send(StartProviderCommand(provider_id=provider))
    query = GetProviderToolsQuery(provider_id=provider)
    tools = ctx.query_bus.execute(query)

    # Apply tool access filtering
    filtered_tools = resolver.filter_tools(provider_id=provider, tools=tools)

    if len(filtered_tools) < len(tools):
        filtered_count = len(tools) - len(filtered_tools)
        TOOLS_FILTERED_TOTAL.set(filtered_count, provider=provider)
        logger.debug(
            "tools_filtered_by_policy: provider_id=%s, total=%d, visible=%d, filtered=%d",
            provider,
            len(tools),
            len(filtered_tools),
            filtered_count,
        )

    return {
        "provider": provider,
        "state": provider_obj.state.value,
        "predefined": False,
        "tools": [t.to_dict() for t in filtered_tools],
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
        """Get tool schemas (JSON Schema) for a provider.

        CHOOSE THIS when: you need tool names and input schemas before calling.
        CHOOSE hangar_details when: you need provider config, health, or runtime info.
        CHOOSE hangar_call when: you already know the tool name and want to invoke it.

        Side effects: May start a cold provider to discover tools.

        Args:
            provider: str - Provider ID or Group ID

        Returns:
            Provider: {
                provider: str,
                state: str,
                predefined: bool,
                tools: [{name: str, description: str, inputSchema: object}]
            }
            Group: {
                provider: str,
                group: true,
                tools: [{name: str, description: str, inputSchema: object}]
            }
            Error: ValueError with "unknown_provider: <id>" or "no_healthy_members_in_group: <id>"

        Example:
            hangar_tools("math")
            # {"provider": "math", "state": "ready", "predefined": false,
            #  "tools": [{"name": "add", "description": "Add two numbers",
            #             "inputSchema": {"properties": {"a": {"type": "number"}, "b": {"type": "number"}}}}]}

            hangar_tools("llm-group")
            # {"provider": "llm-group", "group": true, "tools": [...]}

            hangar_tools("unknown")
            # Error: unknown_provider: unknown
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

        CHOOSE THIS when: you need provider config, health history, or group membership.
        CHOOSE hangar_tools when: you need tool schemas for invoking.
        CHOOSE hangar_status when: you need quick overview of all providers.

        Side effects: None (read-only).

        Args:
            provider: str - Provider ID or Group ID

        Returns:
            Provider: {
                provider: str,
                state: str,
                mode: str,
                alive: bool,
                tools: [{name, description, inputSchema}],
                health: {consecutive_failures: int, last_check: str, ...},
                idle_time: float,
                meta: object
            }
            Group: {
                group_id: str,
                description: str,
                state: str,
                strategy: str,
                min_healthy: int,
                healthy_count: int,
                total_members: int,
                is_available: bool,
                circuit_open: bool,
                members: [{id, state, in_rotation, weight, priority, consecutive_failures}]
            }
            Error: ValueError with "unknown_provider: <id>"

        Example:
            hangar_details("math")
            # {"provider": "math", "state": "ready", "mode": "subprocess",
            #  "alive": true, "tools": [...], "health": {"consecutive_failures": 0},
            #  "idle_time": 12.5, "meta": {}}

            hangar_details("llm-group")
            # {"group_id": "llm-group", "state": "ready", "strategy": "round_robin",
            #  "healthy_count": 2, "total_members": 3, "members": [...]}

            hangar_details("unknown")
            # Error: unknown_provider: unknown
        """
        ctx = get_context()

        if ctx.group_exists(provider):
            return ctx.get_group(provider).to_status_dict()

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        query = GetProviderQuery(provider_id=provider)
        result = ctx.query_bus.execute(query).to_dict()

        # Add tool access policy summary
        resolver = get_tool_access_resolver()
        result["tools_policy"] = resolver.get_policy_summary(provider)

        # Filter tools in the response if present
        if "tools" in result and result["tools"]:
            original_count = len(result["tools"])
            result["tools"] = resolver.filter_tool_dicts(provider, result["tools"])
            filtered_count = original_count - len(result["tools"])
            if filtered_count > 0:
                result["tools_policy"]["filtered_count"] = filtered_count

        return result

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

        CHOOSE THIS when: warming multiple providers before latency-sensitive batch.
        CHOOSE hangar_start when: starting a specific provider or group.
        CHOOSE hangar_call when: invoking tools (auto-starts, latency acceptable).
        SKIP THIS for normal use - hangar_call auto-starts providers.

        Side effects: Starts specified provider processes. Groups are skipped.

        Args:
            providers: str - Comma-separated provider IDs, or null to warm all

        Returns:
            {
                warmed: list[str],
                already_warm: list[str],
                failed: list[{id: str, error: str}],
                summary: str
            }

        Example:
            hangar_warm("math,sqlite")
            # {"warmed": ["math"], "already_warm": ["sqlite"], "failed": [],
            #  "summary": "Warmed 1 providers, 1 already warm, 0 failed"}

            hangar_warm("unknown,math")
            # {"warmed": ["math"], "already_warm": [],
            #  "failed": [{"id": "unknown", "error": "Provider not found"}],
            #  "summary": "Warmed 1 providers, 0 already warm, 1 failed"}

            hangar_warm()
            # {"warmed": ["math", "sqlite"], "already_warm": [], "failed": [],
            #  "summary": "Warmed 2 providers, 0 already warm, 0 failed"}
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
            except Exception as e:  # noqa: BLE001 -- fault-barrier: single provider warm failure must not crash batch
                failed.append({"id": provider_id, "error": str(e)[:100]})

        return {
            "warmed": warmed,
            "already_warm": already_warm,
            "failed": failed,
            "summary": f"Warmed {len(warmed)} providers, {len(already_warm)} already warm, {len(failed)} failed",
        }
