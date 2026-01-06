"""Provider interaction tools: tools, invoke, details.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from ...application.commands import InvokeToolCommand, StartProviderCommand
from ...application.mcp.tooling import chain_validators, key_registry_invoke, mcp_tool_wrapper
from ...domain.model import ProviderGroup
from ...infrastructure.query_bus import GetProviderQuery, GetProviderToolsQuery
from ..context import get_context
from ..validation import (
    check_rate_limit,
    tool_error_hook,
    tool_error_mapper,
    validate_arguments_input,
    validate_provider_id_input,
    validate_timeout_input,
    validate_tool_name_input,
)

# =============================================================================
# Constants
# =============================================================================

DEFAULT_GROUP_RETRY_ATTEMPTS = 2
"""Number of retry attempts when invoking tool on group members."""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Default timeout for tool invocation."""


# =============================================================================
# Helper Functions
# =============================================================================


def _get_tools_for_group(provider: str) -> Dict[str, Any]:
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


def _get_tools_for_provider(provider: str) -> Dict[str, Any]:
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


def _invoke_on_provider(provider: str, tool: str, arguments: Dict, timeout: float) -> Dict[str, Any]:
    """Invoke tool on a single provider."""
    ctx = get_context()
    command = InvokeToolCommand(
        provider_id=provider,
        tool_name=tool,
        arguments=arguments,
        timeout=timeout,
    )
    return ctx.command_bus.send(command)


def _invoke_on_group(group_id: str, tool: str, arguments: Dict, timeout: float) -> Dict[str, Any]:
    """Invoke a tool on a provider group with load balancing."""
    ctx = get_context()
    group = ctx.get_group(group_id)

    if not group.is_available:
        raise ValueError(f"group_not_available: {group_id} (state={group.state.value})")

    selected = group.select_member()
    if not selected:
        raise ValueError(f"no_healthy_members_in_group: {group_id}")

    return _invoke_with_retry(group, tool, arguments, timeout)


def _invoke_with_retry(
    group: ProviderGroup,
    tool: str,
    arguments: Dict,
    timeout: float,
    max_attempts: int = DEFAULT_GROUP_RETRY_ATTEMPTS,
) -> Dict[str, Any]:
    """Invoke tool with retry on different group members."""
    first_error: Optional[Exception] = None
    tried_members: set = set()

    for _ in range(max_attempts):
        selected = group.select_member()
        if not selected or selected.provider_id in tried_members:
            break

        tried_members.add(selected.provider_id)

        try:
            result = _invoke_on_provider(selected.provider_id, tool, arguments, timeout)
            group.report_success(selected.provider_id)
            return result
        except Exception as e:
            group.report_failure(selected.provider_id)
            first_error = first_error or e

    raise first_error or ValueError("no_healthy_members_in_group")


# =============================================================================
# Tool Registration
# =============================================================================


def register_provider_tools(mcp: FastMCP) -> None:
    """Register provider interaction tools with MCP server."""

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

    @mcp.tool(name="registry_invoke")
    @mcp_tool_wrapper(
        tool_name="registry_invoke",
        rate_limit_key=key_registry_invoke,
        check_rate_limit=check_rate_limit,
        validate=chain_validators(
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_provider_id_input(
                provider
            ),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_tool_name_input(tool),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_arguments_input(
                arguments or {}
            ),
            lambda provider, tool, arguments=None, timeout=DEFAULT_TIMEOUT_SECONDS: validate_timeout_input(timeout),
        ),
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_invoke(
        provider: str,
        tool: str,
        arguments: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict:
        """
        Invoke a tool on a provider or provider group.

        This is a COMMAND operation - it may have side effects.

        Args:
            provider: Provider ID or Group ID
            tool: Tool name
            arguments: Tool arguments (default: empty dict)
            timeout: Timeout in seconds (default: 30.0)

        Returns:
            Tool result

        Raises:
            ValueError: If provider ID is unknown or inputs are invalid
        """
        ctx = get_context()
        args = arguments or {}

        if ctx.group_exists(provider):
            return _invoke_on_group(provider, tool, args, timeout)

        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        return _invoke_on_provider(provider, tool, args, timeout)

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
