"""Agent policy push endpoint.

Accepts tool policy bundles from the hangar-agent and applies them
to the ToolAccessResolver so that the approval gate and tool filtering
are enforced.

Endpoint:
  POST /agent/policy  -- Push a tool policy bundle from the agent

The agent authenticates via the X-Hangar-Agent-Internal header.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...domain.services import get_tool_access_resolver
from ...domain.value_objects import ToolAccessPolicy
from ...logging_config import get_logger
from .serializers import HangarJSONResponse

logger = get_logger(__name__)

# Map cloud policy actions to ToolAccessPolicy fields.
_ACTION_REQUIRE_APPROVAL = "require_approval"
_ACTION_DENY = "deny"
_ACTION_AUDIT = "audit"
_ACTION_ALLOW = "allow"


async def push_policy(request: Request) -> HangarJSONResponse:
    """Accept a tool policy bundle from the agent.

    Expected JSON body:
        {
            "version": 1,
            "tool_policies": [
                {"provider_id": "*", "tool_name": "power", "action": "require_approval",
                 "approval_timeout_seconds": 300},
                {"provider_id": "*", "tool_name": "divide", "action": "audit"},
                {"provider_id": "*", "tool_name": "*", "action": "allow"}
            ]
        }
    """
    # Only accept from agent (internal header)
    if request.headers.get("x-hangar-agent-internal") != "true":
        return HangarJSONResponse({"error": "forbidden"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return HangarJSONResponse({"error": "invalid JSON"}, status_code=400)

    tool_policies = body.get("tool_policies", [])
    version = body.get("version", 0)

    if not isinstance(tool_policies, list):
        return HangarJSONResponse(
            {"error": "tool_policies must be a list"}, status_code=400
        )

    resolver = get_tool_access_resolver()

    # Group policies by provider_id
    # provider_id="*" means global (applies to all providers)
    per_provider: dict[str, dict] = {}  # provider_id -> {allow, deny, approval, timeout, channel}

    for tp in tool_policies:
        pid = tp.get("provider_id", "*")
        action = tp.get("action", "allow")
        tool_name = tp.get("tool_name", "*")
        timeout = tp.get("approval_timeout_seconds", 300)

        if pid not in per_provider:
            per_provider[pid] = {
                "allow": [],
                "deny": [],
                "approval": [],
                "timeout": timeout,
            }

        entry = per_provider[pid]
        if action == _ACTION_DENY:
            entry["deny"].append(tool_name)
        elif action == _ACTION_REQUIRE_APPROVAL:
            entry["approval"].append(tool_name)
            entry["timeout"] = timeout
        elif action == _ACTION_AUDIT:
            # Audit = allow (no blocking) -- just let it through
            entry["allow"].append(tool_name)
        elif action == _ACTION_ALLOW:
            entry["allow"].append(tool_name)

    # Apply to resolver
    applied_count = 0
    for pid, entry in per_provider.items():
        policy = ToolAccessPolicy(
            allow_list=tuple(entry["allow"]) if entry["allow"] else (),
            deny_list=tuple(entry["deny"]) if entry["deny"] else (),
            approval_list=tuple(entry["approval"]) if entry["approval"] else (),
            approval_timeout_seconds=entry["timeout"],
            approval_channel="dashboard",
        )

        if pid == "*":
            # Global policy: apply to all known providers
            all_providers = resolver._provider_policies.copy()
            # Also set a special "_global" key for the executor to check
            resolver.set_provider_policy("_global", policy)
            applied_count += 1
        else:
            resolver.set_provider_policy(pid, policy)
            applied_count += 1

    logger.info(
        "agent_policy_applied",
        version=version,
        tool_policies_count=len(tool_policies),
        providers_updated=applied_count,
    )

    return HangarJSONResponse({
        "status": "ok",
        "version": version,
        "applied": applied_count,
    })


agent_policy_routes = [
    Route("/", push_policy, methods=["POST"]),
]
