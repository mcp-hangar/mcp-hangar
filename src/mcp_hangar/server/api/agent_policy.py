# pyright: reportAny=false, reportExplicitAny=false

"""Agent policy push endpoint.

Accepts tool policy bundles from the hangar-agent and applies them
to the ToolAccessResolver so that the approval gate and tool filtering
are enforced.

Endpoint:
  POST /agent/policy  -- Push a tool policy bundle from the agent
"""

from datetime import UTC, datetime
from typing import TypedDict, cast

from starlette.requests import Request
from starlette.routing import Route

from ...domain.events import PolicyPushRejected
from ...domain.exceptions import AccessDeniedError, MissingCredentialsError
from ...domain.services import get_tool_access_resolver
from ...domain.value_objects.security import Principal
from ...domain.value_objects import ToolAccessPolicy
from ...infrastructure.event_bus import get_event_bus
from ...logging_config import get_logger
from ..context import get_context
from .serializers import HangarJSONResponse

logger = get_logger(__name__)

# Map cloud policy actions to ToolAccessPolicy fields.
_ACTION_REQUIRE_APPROVAL = "require_approval"
_ACTION_DENY = "deny"
_ACTION_AUDIT = "audit"
_ACTION_ALLOW = "allow"


class PolicyPushBody(TypedDict, total=False):
    version: int
    tool_policies: list[object]


class PolicyItem(TypedDict, total=False):
    mcp_server_id: str
    action: str
    tool_name: str
    approval_timeout_seconds: int


class PolicyEntry(TypedDict):
    allow: list[str]
    deny: list[str]
    approval: list[str]
    timeout: int


def _publish_policy_push_rejected(principal_id: str, reason: str) -> None:
    get_event_bus().publish(
        PolicyPushRejected(
            principal_id=principal_id,
            reason=reason,
            timestamp=datetime.now(UTC),
        )
    )


def _require_authenticated_principal(request: Request) -> Principal:
    auth_context = getattr(request.state, "auth", None)
    principal = getattr(auth_context, "principal", None)

    if principal is None or principal.is_anonymous():
        _publish_policy_push_rejected("anonymous", "authentication_required")
        raise MissingCredentialsError("Authentication required")

    return principal


def _authorize_policy_write(principal: Principal) -> None:
    context = get_context()
    auth_components = getattr(context, "auth_components", None)
    authz_middleware = getattr(auth_components, "authz_middleware", None)

    if authz_middleware is not None:
        try:
            authz_middleware.authorize(
                principal=principal,
                action="write",
                resource_type="policy",
                resource_id="*",
            )
        except AccessDeniedError:
            _publish_policy_push_rejected(principal.id.value, "policy_write_permission_required")
            raise
        return

    _publish_policy_push_rejected(principal.id.value, "authorization_unavailable")
    raise AccessDeniedError(
        principal_id=principal.id.value,
        action="write",
        resource="policy:*",
        reason="policy:write permission required",
    )


async def push_policy(request: Request) -> HangarJSONResponse:
    """Accept a tool policy bundle from the agent.

    Expected JSON body:
        {
            "version": 1,
            "tool_policies": [
                {"mcp_server_id": "*", "tool_name": "power", "action": "require_approval",
                 "approval_timeout_seconds": 300},
                {"mcp_server_id": "*", "tool_name": "divide", "action": "audit"},
                {"mcp_server_id": "*", "tool_name": "*", "action": "allow"}
            ]
        }
    """
    principal = _require_authenticated_principal(request)
    _authorize_policy_write(principal)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return HangarJSONResponse({"error": "invalid JSON"}, status_code=400)

    if not isinstance(body, dict):
        return HangarJSONResponse({"error": "invalid JSON"}, status_code=400)

    payload = cast(PolicyPushBody, cast(object, body))
    tool_policies = payload.get("tool_policies", [])
    version = payload.get("version", 0)

    resolver = get_tool_access_resolver()

    # Group policies by mcp_server_id
    # mcp_server_id="*" means global (applies to all mcp_servers)
    per_mcp_server: dict[str, PolicyEntry] = {}  # mcp_server_id -> {allow, deny, approval, timeout}

    for tp in tool_policies:
        if not isinstance(tp, dict):
            continue
        item = cast(PolicyItem, cast(object, tp))
        pid = item.get("mcp_server_id", "*")
        action = item.get("action", "allow")
        tool_name = item.get("tool_name", "*")
        timeout = item.get("approval_timeout_seconds", 300)

        if pid not in per_mcp_server:
            per_mcp_server[pid] = {
                "allow": [],
                "deny": [],
                "approval": [],
                "timeout": timeout,
            }

        entry = per_mcp_server[pid]
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
    for pid, entry in per_mcp_server.items():
        policy = ToolAccessPolicy(
            allow_list=tuple(entry["allow"]) if entry["allow"] else (),
            deny_list=tuple(entry["deny"]) if entry["deny"] else (),
            approval_list=tuple(entry["approval"]) if entry["approval"] else (),
            approval_timeout_seconds=entry["timeout"],
            approval_channel="dashboard",
        )

        if pid == "*":
            # Global policy: apply to all known mcp_servers
            # Also set a special "_global" key for the executor to check
            resolver.set_mcp_server_policy("_global", policy)
            applied_count += 1
        else:
            resolver.set_mcp_server_policy(pid, policy)
            applied_count += 1

    logger.info(
        "agent_policy_applied",
        version=version,
        tool_policies_count=len(tool_policies),
        mcp_servers_updated=applied_count,
    )

    return HangarJSONResponse(
        {
            "status": "ok",
            "version": version,
            "applied": applied_count,
        }
    )


agent_policy_routes = [
    Route("/", push_policy, methods=["POST"]),
]
