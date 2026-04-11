"""MCP permission prompt tool for Claude Code integration.

Registered conditionally when enterprise.approvals.channel is "mcp_prompt".
Implements the Claude Code permission prompt spec:
  - {"behavior": "allow"} to approve
  - {"behavior": "deny", "message": str} to deny

Internally delegates to ApprovalGateService.check() via application context.
The "human" in this case approves via dashboard or Slack while Claude Code
waits on the tool call response.
"""

from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)


async def hangar_approve_prompt(
    provider_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    _context: Any = None,
) -> dict[str, Any]:
    """MCP tool implementing the Claude Code permission prompt spec.

    Args:
        provider_id: Provider whose tool needs approval.
        tool_name: Name of the tool requiring approval.
        arguments: Tool arguments (sanitized before display).
        _context: Application context injected at registration time.

    Returns:
        {"behavior": "allow"} or {"behavior": "deny", "message": str}
    """
    if _context is None or not hasattr(_context, "approval_gate"):
        logger.warning("approve_prompt_no_context")
        return {"behavior": "allow"}

    gate_service = _context.approval_gate
    policy = _context.get_policy_for_provider(provider_id) if hasattr(_context, "get_policy_for_provider") else None

    if policy is None:
        return {"behavior": "allow"}

    try:
        result = await gate_service.check(
            provider_id=provider_id,
            tool_name=tool_name,
            arguments=arguments or {},
            policy=policy,
            correlation_id="",
        )
    except (RuntimeError, OSError, ValueError, TimeoutError):
        logger.warning("approve_prompt_check_failed", exc_info=True)
        return {"behavior": "allow"}

    if result.approved:
        return {"behavior": "allow"}

    return {
        "behavior": "deny",
        "message": result.reason or f"Tool {tool_name} was not approved",
    }
