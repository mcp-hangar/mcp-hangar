"""REST API routes for the approval gate.

Endpoints:
  GET  /enterprise/approvals           - List approvals (filtered by state)
  GET  /enterprise/approvals/{id}      - Get single approval
  POST /enterprise/approvals/{id}/resolve - Approve or deny

Mounted by enterprise bootstrap. Auth via JWT (approval:read/resolve
permissions) or Slack HMAC callback signature on resolve.
"""

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from mcp_hangar.logging_config import get_logger
from mcp_hangar.server.api.serializers import HangarJSONResponse

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
    now = datetime.now(timezone.utc)
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

    from enterprise.approvals.models import ApprovalState

    try:
        state = ApprovalState(state_filter)
    except ValueError:
        return HangarJSONResponse(
            {"error": f"Invalid state: {state_filter}"}, status_code=400
        )

    requests = await service._repository.list_by_state(state, provider_id)
    return HangarJSONResponse([_to_dto(r) for r in requests])


async def get_approval(request: Request) -> HangarJSONResponse:
    """Get a single approval request by ID."""
    service = _get_approval_service(request)
    approval_id = request.path_params["approval_id"]

    approval = await service._repository.get(approval_id)
    if approval is None:
        return HangarJSONResponse(
            {"error": "Approval not found"}, status_code=404
        )

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
        return HangarJSONResponse(
            {"error": "decision must be 'approve' or 'deny'"}, status_code=400
        )

    # Check if already resolved
    existing = await service._repository.get(approval_id)
    if existing is None:
        return HangarJSONResponse({"error": "Approval not found"}, status_code=404)
    if existing.is_terminal():
        return HangarJSONResponse(
            {"error": "Approval already resolved", "state": existing.state.value},
            status_code=409,
        )

    decided_by = _extract_principal(request)
    approved = decision == "approve"

    success = await service.resolve(approval_id, approved, decided_by, reason)
    if not success:
        return HangarJSONResponse(
            {"error": "Failed to resolve approval"}, status_code=409
        )

    updated = await service._repository.get(approval_id)
    state = updated.state.value if updated else decision

    return HangarJSONResponse({
        "approval_id": approval_id,
        "state": state,
    })


async def _handle_slack_callback(
    request: Request, service: Any, approval_id: str
) -> HangarJSONResponse:
    """Handle Slack interactive message callback."""
    # Verify timestamp freshness (replay protection)
    timestamp_str = request.headers.get("x-slack-request-timestamp", "")
    try:
        timestamp = int(timestamp_str)
    except (ValueError, TypeError):
        return HangarJSONResponse({"error": "Invalid timestamp"}, status_code=401)

    if abs(time.time() - timestamp) > 300:
        return HangarJSONResponse(
            {"error": "Stale request"}, status_code=401
        )

    # Verify HMAC signature
    raw_body = await request.body()
    signing_secret = _get_slack_signing_secret(request)
    if not signing_secret:
        return HangarJSONResponse(
            {"error": "Slack signing not configured"}, status_code=500
        )

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
        return HangarJSONResponse(
            {"error": "Already resolved"}, status_code=409
        )

    return HangarJSONResponse({"approval_id": approval_id, "state": "resolved"})


def _extract_principal(request: Request) -> str:
    """Extract principal identity from request auth context."""
    # Check for auth middleware populated identity
    if hasattr(request, "state") and hasattr(request.state, "principal_id"):
        return request.state.principal_id
    return request.headers.get("x-principal-id", "unknown")


def _get_slack_signing_secret(request: Request) -> str | None:
    """Get Slack signing secret from app config."""
    if hasattr(request.app.state, "slack_signing_secret"):
        return request.app.state.slack_signing_secret
    return None


approval_routes = [
    Route("/enterprise/approvals", list_approvals, methods=["GET"]),
    Route("/enterprise/approvals/{approval_id:str}", get_approval, methods=["GET"]),
    Route(
        "/enterprise/approvals/{approval_id:str}/resolve",
        resolve_approval,
        methods=["POST"],
    ),
]
