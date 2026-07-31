"""REST API routes for the approval gate.

Endpoints:
  GET  /approvals           - List approvals (filtered by state)
  GET  /approvals/{id}      - Get single approval
  POST /approvals/{id}/resolve - Approve or deny

Mounted by the server component loader. One authentication path: the platform's
own, with `approval:read` / `approval:resolve` enforced in the command handler.
Vendor callbacks terminate in an adapter outside core and arrive here as ordinary
authenticated requests.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from typing import Any

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

    One authentication path: the platform's own, requiring `approval:resolve`.

    This route used to branch on the presence of an `X-Slack-Signature` header
    and hand control to a vendor-specific verifier. Both branches were
    individually sound, but the shape was not: an unauthenticated caller chose
    which authentication mechanism ran. Vendor callbacks now terminate in an
    adapter outside core, which verifies the vendor's signature, maps the vendor
    identity onto a Hangar principal, and calls this endpoint with an ordinary
    token. Core does not know Slack exists.

    Body:
        decision: "approve" | "deny"
        reason: Optional string
    """
    service = _get_approval_service(request)
    approval_id = request.path_params["approval_id"]

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
    if result.outcome is ResolveOutcome.EXPIRED:
        # 409 rather than 404: the approval exists and the caller may hold a
        # perfectly good token for it -- what has run out is the window.
        return HangarJSONResponse(
            {"error": "Approval expired", "state": result.state},
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


approval_routes = [
    Route("/approvals", list_approvals, methods=["GET"]),
    Route("/approvals/{approval_id:str}", get_approval, methods=["GET"]),
    Route(
        "/approvals/{approval_id:str}/resolve",
        resolve_approval,
        methods=["POST"],
    ),
]
