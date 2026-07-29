"""EPIC A-2919 WS-0: approval resolution was neither authorized nor attributed.

Written red against the unfixed tree, and kept as the regression suite for the
WS-1/WS-2 fix that lands with them. What they pinned:

**Authorization.** ``approval:resolve`` existed as a permission -- defined in
``auth/roles.py``, mapped from its string form, granted to a role -- and was
checked nowhere. Grepping the whole package for an authorization call found
nothing::

    grep -rn "authorize\\|authz" src/mcp_hangar/approvals/   ->   (no matches)

Any principal holding a valid token could decide any approval given its id. The
enforcement pattern already existed one package over, in
``server/api/mcp_servers.py``; the approvals routes never adopted it.

**Attribution.** ``_extract_principal`` looked for ``request.state.principal_id``,
but the authentication middleware attaches ``request.state.auth`` and *nothing in
the tree ever set* ``principal_id`` -- so that branch could never be taken and the
function always fell through to the client-supplied ``x-principal-id`` header,
defaulting to the literal ``"unknown"``. Not a fallback for unauthenticated
callers: the only path, including for fully authenticated ones. That value is
what landed in ``decided_by``, in the provenance chain.

**The status code lied about the damage.** ``ApprovalGateService.resolve`` writes
the decision and *then* releases the in-process hold, so with no waiter the caller
received 409 "Failed to resolve approval" against an approval already recorded as
decided. The primary test therefore asserts durable state, not the HTTP status.

Tests assert the observable contract rather than the implementation: they install
a real application context whose authorizer denies, and check what the caller and
the ledger end up with. ``get_context()`` resolves its module global per call, so
this works regardless of how the route imports it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.approvals.api.routes import approval_routes
from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.auth.infrastructure.middleware import AuthContext
from mcp_hangar.domain.exceptions import AccessDeniedError, MCPError
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server import context as context_mod
from mcp_hangar.server.api.middleware import error_handler
from datetime import UTC

DENIED_ACTION = "resolve"
DENIED_RESOURCE = "approval"


class _InMemoryRepository:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    async def save(self, request: Any) -> None:
        self._store[request.approval_id] = request

    async def get(self, approval_id: str) -> Any:
        return self._store.get(approval_id)

    async def list_pending(self, mcp_server_id: str | None = None) -> list[Any]:
        return [r for r in self._store.values() if r.state == ApprovalState.PENDING]

    async def list_by_state(self, state: Any, mcp_server_id: str | None = None) -> list[Any]:
        return [r for r in self._store.values() if r.state == state]

    async def update_state(
        self, approval_id: str, state: Any, decided_by: str, decided_at: Any, reason: Any
    ) -> None:
        r = self._store.get(approval_id)
        if r:
            r.state = state
            r.decided_by = decided_by
            r.decided_at = decided_at
            r.reason = reason


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _DenyingAuthorizer:
    """Mirrors ``AuthorizationMiddleware.authorize``: raises when denied.

    Deliberately denies everything. A principal that holds no permission at all
    is the sharpest form of the question these tests ask: does the route consult
    an authorizer *at all*?
    """

    calls: list[tuple[str, str]]

    def authorize(
        self, *, principal: Principal, action: str, resource_type: str, resource_id: str
    ) -> None:
        self.calls.append((resource_type, action))
        raise AccessDeniedError(
            principal_id=str(principal.id),
            action=action,
            resource=f"{resource_type}:{resource_id}",
        )


def _principal() -> Principal:
    return Principal(id=PrincipalId("alice"), type=PrincipalType.USER)


@contextmanager
def _app_context_denying_everything():
    """Install a real application context whose authorizer denies.

    ``get_context()`` resolves its module global on each call, so assigning it
    here reaches every caller regardless of import style -- including code that
    does not exist yet.
    """
    authorizer = _DenyingAuthorizer(calls=[])
    auth_components = MagicMock()
    auth_components.enabled = True
    auth_components.authz_middleware = authorizer

    ctx = context_mod.ApplicationContext(runtime=MagicMock())
    ctx.auth_components = auth_components

    previous = context_mod._context
    context_mod._context = ctx
    try:
        yield authorizer
    finally:
        context_mod._context = previous


class _AttachPrincipal(BaseHTTPMiddleware):
    """Attach ``request.state.auth`` the way the real auth middleware does."""

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        request.state.auth = AuthContext(principal=_principal(), auth_method="test")
        return await call_next(request)


def _build_stack(*, attach_principal: bool):
    repo = _InMemoryRepository()
    service = ApprovalGateService(
        repository=repo,
        hold_registry=ApprovalHoldRegistry(),
        event_bus=_FakeEventBus(),
        delivery=NoOpApprovalDelivery(),
    )
    app = Starlette(
        routes=[Mount("/", routes=approval_routes)],
        middleware=[],
        # Same registration the real API router uses (server/api/router.py:71-76),
        # so exception -> status mapping is exercised rather than approximated.
        exception_handlers={MCPError: error_handler, Exception: error_handler},
    )
    if attach_principal:
        app.add_middleware(_AttachPrincipal)
    app.state.approval_gate_service = service
    return repo, service, TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def stack():
    """Authenticated caller: ``request.state.auth`` carries a principal."""
    return _build_stack(attach_principal=True)


@pytest.fixture
def stack_without_auth():
    """Auth-off deployment: no authentication middleware, so no principal.

    This is the shape the API router actually produces when auth is disabled --
    it does not mount authentication at all.
    """
    return _build_stack(attach_principal=False)


async def _pending_approval(repo: _InMemoryRepository, service: ApprovalGateService) -> str:
    """Create one pending approval and return its id."""
    from datetime import datetime, timedelta

    from mcp_hangar.approvals.models import ApprovalRequest

    now = datetime.now(UTC)
    req = ApprovalRequest(
        approval_id="ap-1",
        mcp_server_id="grafana",
        tool_name="delete_dashboard",
        arguments={"uid": "abc"},
        arguments_hash="sha256:test",
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
        state=ApprovalState.PENDING,
        channel="noop",
    )
    await repo.save(req)
    return str(req.approval_id)


class TestResolveRequiresApprovalResolvePermission:
    """F1: the permission is defined, granted, and never enforced."""

    async def test_unauthorized_caller_cannot_decide_an_approval(self, stack) -> None:
        """The damage, asserted on durable state rather than on the status code.

        ``ApprovalGateService.resolve`` writes the decision first and releases the
        in-process hold second (``service.py:232`` then ``:234``). With no waiter
        registered the hold release returns False and the route answers 409
        "Failed to resolve approval" -- *after* the approval has already been
        recorded as decided. So the HTTP status is not a safe thing to assert on:
        it can say "failed" while the ledger says "approved by whoever asked".

        This asserts the state, which is what actually matters.
        """
        repo, service, client = stack
        approval_id = await _pending_approval(repo, service)

        with _app_context_denying_everything():
            client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approve"})

        stored = await repo.get(approval_id)
        assert stored.state == ApprovalState.PENDING, (
            f"an unauthorized principal moved the approval to {stored.state} "
            f"(decided_by={stored.decided_by!r}); the decision is durable even "
            "when the caller is told it failed"
        )

    async def test_resolve_is_refused_with_403(self, stack) -> None:
        """The epic's stated WS-0 acceptance, kept separate on purpose.

        Today this returns 409 rather than 403 -- not because anything refused
        the caller, but because the decision succeeded and only the hold release
        failed. Once WS-1 puts authorization in front, this becomes a real 403.
        """
        repo, service, client = stack
        approval_id = await _pending_approval(repo, service)

        with _app_context_denying_everything() as authorizer:
            resp = client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approve"})

        assert resp.status_code == 403, (
            f"resolve returned {resp.status_code}; authorizer consulted: "
            f"{authorizer.calls or 'never'}"
        )

    async def test_the_authorizer_is_consulted_at_all(self, stack) -> None:
        """Narrower companion: pins *why* the test above fails.

        A 403 could in principle arrive from something other than authorization.
        This asserts the route actually asks, so a future refactor cannot satisfy
        the contract by accident.
        """
        repo, service, client = stack
        approval_id = await _pending_approval(repo, service)

        with _app_context_denying_everything() as authorizer:
            client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approve"})

        assert (DENIED_RESOURCE, DENIED_ACTION) in authorizer.calls, (
            "resolve_approval never called authorize(); "
            f"observed calls: {authorizer.calls}"
        )


class TestAuthDisabledStillResolves:
    """Guards the #600 shape: fail-closed on the API is fail-OPEN on enforcement.

    When auth is disabled the API router does not mount authentication, so no
    principal is ever attached. An unconditional identity requirement would 401
    every resolution with no credential able to fix it -- approvals would simply
    never be decided, which is the failure mode that looks safe and is not.

    Written after this regression was introduced and caught by the existing suite:
    eight approval tests went red with ``Authentication required``. It is a test
    rather than a comment because the next person to tighten this will be as sure
    as I was.
    """

    async def test_resolution_succeeds_without_a_principal(self, stack_without_auth) -> None:
        repo, service, client = stack_without_auth
        approval_id = await _pending_approval(repo, service)

        # No app context and no authentication middleware -> auth off.
        resp = client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approve"})

        assert resp.status_code != 401, "auth is disabled; refusing here decides nothing"
        stored = await repo.get(approval_id)
        assert stored.state == ApprovalState.APPROVED

    async def test_decided_by_is_the_system_principal_not_a_sentinel(self, stack_without_auth) -> None:
        """Auth off records an explicit server-side identity, never ``unknown``."""
        repo, service, client = stack_without_auth
        approval_id = await _pending_approval(repo, service)

        client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approve"})

        stored = await repo.get(approval_id)
        assert stored.decided_by not in ("unknown", None, "")
        assert "system" in stored.decided_by.lower(), (
            f"expected an explicit server-side identity, got {stored.decided_by!r}"
        )


class TestDecidedByComesFromAuthenticatedContext:
    """F2: ``decided_by`` is client-attested, always."""

    async def test_header_cannot_set_decided_by(self, stack) -> None:
        repo, service, client = stack
        approval_id = await _pending_approval(repo, service)

        client.post(
            f"/approvals/{approval_id}/resolve",
            json={"decision": "approve"},
            headers={"x-principal-id": "attacker-supplied"},
        )

        stored = await repo.get(approval_id)
        assert stored.decided_by != "attacker-supplied", (
            "decided_by was taken from a client-supplied header and written into "
            "the provenance chain"
        )

    async def test_decided_by_is_never_the_unknown_sentinel(self, stack) -> None:
        repo, service, client = stack
        approval_id = await _pending_approval(repo, service)

        client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approve"})

        stored = await repo.get(approval_id)
        assert stored.decided_by != "unknown", (
            "an approval was recorded as decided by 'unknown' -- the sentinel is "
            "reachable, so the provenance chain can carry no identity at all"
        )
