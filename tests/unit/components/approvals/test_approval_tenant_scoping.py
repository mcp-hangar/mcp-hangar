"""An approval belongs to the tenant that raised it. Authorization by
`approval:resolve` alone let an approver in one tenant see and resolve another
tenant's approvals; these pin the tenant binding on resolve and on the list/get
visibility helper. Auth is disabled here (auth_components=None) so the tenant
check is exercised in isolation from the permission check."""

from datetime import UTC, datetime, timedelta

import pytest

from mcp_hangar.approvals.api.routes import _tenant_visible
from mcp_hangar.approvals.commands.resolve import (
    ResolveApprovalCommand,
    ResolveApprovalHandler,
    ResolveOutcome,
)
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType

from .test_approval_gate_service import FakeRepository


def _principal(tenant):
    return Principal(id=PrincipalId("approver"), type=PrincipalType.USER, tenant_id=tenant)


def _approval(tenant, aid="ap-1"):
    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id=aid,
        provider_id="bank",
        tool_name="wire_transfer",
        arguments={"amount": 1},
        arguments_hash="h",
        requested_at=now,
        expires_at=now + timedelta(seconds=300),
        state=ApprovalState.PENDING,
        channel="dashboard",
        tenant_id=tenant,
    )


async def _handler_with(approval):
    repo = FakeRepository()
    await repo.save(approval)
    reg = ApprovalHoldRegistry()
    await reg.register(approval.approval_id)
    svc = ApprovalGateService(repository=repo, hold_registry=reg, event_bus=None, delivery=None)
    h = ResolveApprovalHandler(svc, auth_components=None)
    return h


class TestResolveTenantScoping:
    @pytest.mark.asyncio
    async def test_foreign_tenant_cannot_resolve_and_cannot_tell_it_exists(self):
        h = await _handler_with(_approval("tenant:a"))
        r = await h.handle(ResolveApprovalCommand(approval_id="ap-1", approved=True, principal=_principal("tenant:b")))
        # NOT_FOUND, not a distinct forbidden -> existence itself is scoped
        assert r.outcome is ResolveOutcome.NOT_FOUND

    @pytest.mark.asyncio
    async def test_same_tenant_resolves(self):
        h = await _handler_with(_approval("tenant:a"))
        r = await h.handle(ResolveApprovalCommand(approval_id="ap-1", approved=True, principal=_principal("tenant:a")))
        assert r.outcome is ResolveOutcome.RESOLVED

    @pytest.mark.asyncio
    async def test_tenantless_caller_cannot_resolve_a_scoped_approval(self):
        h = await _handler_with(_approval("tenant:a"))
        r = await h.handle(ResolveApprovalCommand(approval_id="ap-1", approved=True, principal=_principal(None)))
        assert r.outcome is ResolveOutcome.NOT_FOUND

    @pytest.mark.asyncio
    async def test_untenanted_approval_is_not_scoped(self):
        # single-tenant / auth-off: approval carries no tenant, anyone authorized resolves
        h = await _handler_with(_approval(None))
        r = await h.handle(ResolveApprovalCommand(approval_id="ap-1", approved=True, principal=_principal("tenant:b")))
        assert r.outcome is ResolveOutcome.RESOLVED


class TestListVisibility:
    def test_visible_only_within_tenant(self):
        a = _approval("tenant:a")
        assert _tenant_visible(a, _principal("tenant:a")) is True
        assert _tenant_visible(a, _principal("tenant:b")) is False
        assert _tenant_visible(a, _principal(None)) is False

    def test_untenanted_approval_visible_to_all(self):
        a = _approval(None)
        assert _tenant_visible(a, _principal("tenant:a")) is True
        assert _tenant_visible(a, _principal(None)) is True
