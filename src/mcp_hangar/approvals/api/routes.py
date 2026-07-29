"""REST API routes for the approval gate.

Endpoints:
  GET  /approvals           - List approvals (filtered by state)
  GET  /approvals/{id}      - Get single approval
  POST /approvals/{id}/resolve - Approve or deny

Mounted by the server component loader. Auth via JWT (approval:read/resolve
permissions) or Slack HMAC callback signature on resolve.
"""

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from typing import Any, cast

from starlette.requests import Request
from starlette.routing import Route

from mcp_hangar.auth.http_middleware import get_principal_from_request
from mcp_hangar.domain.exceptions import MissingCredentialsError
from mcp_hangar.domain.value_objects.security import Principal
from mcp_hangar.logging_config import get_logger
from mcp_hangar.server.api.serializers import HangarJSONResponse
from mcp_hangar.server.context import get_context

from ..commands.resolve import ResolveApprovalCommand, ResolveApprovalHandler, ResolveOutcome

logger = get_logger(__name__)


@dataclass
class ApprovalRequestDTO:
    approval_id: str
    provider_id: str
    tool_name: str
    arguments: dict[str, Any]
    state: str
    channel: str
    requested_at: str
    expires_at: str
    expires_in_seconds: int
    decided_by: str | None
    decided_at: str | None
    reason: str | None


def _to_dto(request: Any) -> dict[str, Any]:
    """Convert ApprovalRequest model to DTO dict."""
    now = datetime.now(UTC)
    expires_in = max(0, int((request.expires_at - now).total_seconds()))
    dto = ApprovalRequestDTO(
        approval_id=request.approval_id,
        provider_id=request.provider_id,
        tool_name=request.tool_name,
        arguments=request.arguments,
        state=request.state.value if hasattr(request.state, "value") else str(request.state),
        channel=request.channel,
        requested_at=request.requested_at.isoformat(),
        expires_at=request.expires_at.isoformat(),
        expires_in_seconds=expires_in,
        decided_by=request.decided_by,
        decided_at=request.decided_at.isoformat() if request.decided_at else None,
        reason=request.reason,
    )
    return asdict(dto)


def _get_approval_service(request: Request) -> Any:
    """Extract ApprovalGateService from app state."""
    return request.app.state.approval_gate_service


async def list_approvals(request: Request) -> HangarJSONResponse:
    """List approval requests filtered by state.

    Query params:
        state: Filter by state (default: pending). One of: pending, approved, denied, expired.
        provider_id: Optional provider filter.
    """
    service = _get_approval_service(request)
    state_filter = request.query_params.get("state", "pending")
    provider_id = request.query_params.get("provider_id")

    from mcp_hangar.approvals.models import ApprovalState

    try:
        state = ApprovalState(state_filter)
    except ValueError:
        return HangarJSONResponse({"error": f"Invalid state: {state_filter}"}, status_code=400)

    requests = await service._repository.list_by_state(state, provider_id)
    return HangarJSONResponse([_to_dto(r) for r in requests])


async def get_approval(request: Request) -> HangarJSONResponse:
    """Get a single approval request by ID."""
    service = _get_approval_service(request)
    approval_id = request.path_params["approval_id"]

    approval = await service._repository.get(approval_id)
    if approval is None:
        return HangarJSONResponse({"error": "Approval not found"}, status_code=404)

    return HangarJSONResponse(_to_dto(approval))


async def resolve_approval(request: Request) -> HangarJSONResponse:
    """Resolve (approve or deny) a pending approval.

    Accepts either:
      - JWT auth with approval:resolve permission
      - Slack HMAC callback (X-Slack-Signature header)

    Body (JSON path):
        decision: "approve" | "deny"
        reason: Optional string

    Body (Slack callback path):
        payload: URL-encoded JSON with actions[0].action_id
    """
    service = _get_approval_service(request)
    approval_id = request.path_params["approval_id"]

    # Check if this is a Slack callback
    slack_signature = request.headers.get("x-slack-signature")
    if slack_signature:
        return await _handle_slack_callback(request, service, approval_id)

    # Standard JSON resolution
    body = await request.json()
    decision = body.get("decision")
    reason = body.get("reason")

    if decision not in ("approve", "deny"):
        return HangarJSONResponse({"error": "decision must be 'approve' or 'deny'"}, status_code=400)

    auth_components = _auth_components()
    principal = _require_principal(request, auth_components)

    result = await _resolve_handler(service, auth_components).handle(
        ResolveApprovalCommand(
            approval_id=approval_id,
            approved=decision == "approve",
            principal=principal,
            reason=reason,
        )
    )

    if result.outcome is ResolveOutcome.NOT_FOUND:
        return HangarJSONResponse({"error": "Approval not found"}, status_code=404)
    if result.outcome is ResolveOutcome.ALREADY_TERMINAL:
        return HangarJSONResponse(
            {"error": "Approval already resolved", "state": result.state},
            status_code=409,
        )
    if result.outcome is ResolveOutcome.HOLD_RELEASE_FAILED:
        return HangarJSONResponse({"error": "Failed to resolve approval"}, status_code=409)

    return HangarJSONResponse(
        {
            "approval_id": approval_id,
            "state": result.state if result.state is not None else decision,
        }
    )


async def _handle_slack_callback(request: Request, service: Any, approval_id: str) -> HangarJSONResponse:
    """Handle Slack interactive message callback."""
    # Verify timestamp freshness (replay protection)
    timestamp_str = request.headers.get("x-slack-request-timestamp", "")
    try:
        timestamp = int(timestamp_str)
    except (ValueError, TypeError):
        return HangarJSONResponse({"error": "Invalid timestamp"}, status_code=401)

    if abs(time.time() - timestamp) > 300:
        return HangarJSONResponse({"error": "Stale request"}, status_code=401)

    # Verify HMAC signature
    raw_body = await request.body()
    signing_secret = _get_slack_signing_secret(request)
    if not signing_secret:
        return HangarJSONResponse({"error": "Slack signing not configured"}, status_code=500)

    sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected_sig = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )

    slack_signature = request.headers.get("x-slack-signature", "")
    if not hmac.compare_digest(expected_sig, slack_signature):
        return HangarJSONResponse({"error": "Invalid signature"}, status_code=401)

    # Parse Slack payload
    try:
        from urllib.parse import parse_qs

        body_str = raw_body.decode("utf-8")
        parsed = parse_qs(body_str)
        payload = json.loads(parsed.get("payload", ["{}"])[0])
        actions = payload.get("actions", [])
        if not actions:
            return HangarJSONResponse({"error": "No actions"}, status_code=400)

        action_id = actions[0].get("action_id", "")
        user_id = payload.get("user", {}).get("id", "unknown")
    except (json.JSONDecodeError, KeyError, IndexError):
        return HangarJSONResponse({"error": "Invalid payload"}, status_code=400)

    # Parse action: approve_{id} or deny_{id}
    if action_id.startswith("approve_"):
        approved = True
    elif action_id.startswith("deny_"):
        approved = False
    else:
        return HangarJSONResponse({"error": "Unknown action"}, status_code=400)

    decided_by = f"slack:{user_id}"
    success = await service.resolve(approval_id, approved, decided_by)

    if not success:
        return HangarJSONResponse({"error": "Already resolved"}, status_code=409)

    return HangarJSONResponse({"approval_id": approval_id, "state": "resolved"})


def _require_principal(request: Request, auth_components: Any | None) -> Principal:
    """Return the caller's identity, or refuse -- never invent one.

    Replaces ``_extract_principal``, which read ``request.state.principal_id`` and
    fell back to the ``x-principal-id`` header, defaulting to the literal
    ``"unknown"``. That was not a fallback for unauthenticated callers -- it was
    the only path: the authentication middleware attaches ``request.state.auth``
    and **nothing in the tree ever set** ``principal_id``, so the first branch
    could never be taken. Every recorded ``decided_by`` was therefore either
    client-attested or ``"unknown"``, in the provenance chain.

    The auth-disabled case is deliberately NOT a 401. When auth is off the API
    router does not mount authentication at all, so no principal is ever attached
    -- refusing here would mean no caller could ever resolve an approval, with no
    credential that could fix it. That is #600's exact shape: failing closed on
    the API is failing OPEN on enforcement, because the decision simply never gets
    made. Instead the identity is recorded as the system principal: explicit,
    server-side, and impossible to confuse with a real approver.

    Auth on and no principal is still a refusal.
    """
    principal = get_principal_from_request(request)
    if principal is not None:
        return principal
    if getattr(auth_components, "enabled", False):
        raise MissingCredentialsError("Authentication required")
    return Principal.system()


def _auth_components() -> Any | None:
    """The application's auth components, or None when there is no app context.

    No context means no auth (tests mounting the routes directly, stdio); the
    callers below treat that as auth-disabled rather than as a failure.
    """
    try:
        return getattr(get_context(), "auth_components", None)
    except Exception:  # noqa: BLE001 -- absence of a context is not an error here
        return None


def _resolve_handler(service: Any, auth_components: Any | None) -> ResolveApprovalHandler:
    """Build the resolution handler for this request.

    Constructed per request rather than wired at bootstrap: the handler holds no
    state, and this keeps every existing caller that mounts the routes with only
    ``app.state.approval_gate_service`` working unchanged.
    """
    return ResolveApprovalHandler(service, auth_components=auth_components)


def _get_slack_signing_secret(request: Request) -> str | None:
    """Get Slack signing secret from app config."""
    if hasattr(request.app.state, "slack_signing_secret"):
        return cast(str | None, request.app.state.slack_signing_secret)
    return None


approval_routes = [
    Route("/approvals", list_approvals, methods=["GET"]),
    Route("/approvals/{approval_id:str}", get_approval, methods=["GET"]),
    Route(
        "/approvals/{approval_id:str}/resolve",
        resolve_approval,
        methods=["POST"],
    ),
]
