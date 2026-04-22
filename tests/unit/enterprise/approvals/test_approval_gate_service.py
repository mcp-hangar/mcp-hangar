"""Unit tests for ApprovalGateService."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from enterprise.approvals.hold_registry import ApprovalHoldRegistry
from enterprise.approvals.models import ApprovalRequest, ApprovalResult, ApprovalState
from enterprise.approvals.service import ApprovalGateService
from mcp_hangar.domain.events import (
    ToolApprovalDenied,
    ToolApprovalExpired,
    ToolApprovalGranted,
    ToolApprovalRequested,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class FakeRepository:
    """In-memory repository for testing."""

    def __init__(self):
        self._store: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self._store[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._store.get(approval_id)

    async def list_pending(self, mcp_server_id=None):
        return [
            r for r in self._store.values()
            if r.state == ApprovalState.PENDING
            and (mcp_server_id is None or r.mcp_server_id == mcp_server_id)
        ]

    async def list_by_state(self, state, mcp_server_id=None):
        return [
            r for r in self._store.values()
            if r.state == state
            and (mcp_server_id is None or r.mcp_server_id == mcp_server_id)
        ]

    async def update_state(self, approval_id, state, decided_by, decided_at, reason):
        req = self._store.get(approval_id)
        if req:
            req.state = state
            req.decided_by = decided_by
            req.decided_at = decided_at
            req.reason = reason


class FakeDelivery:
    """Delivery that records send calls."""

    def __init__(self):
        self.sent: list[ApprovalRequest] = []

    async def send(self, request: ApprovalRequest) -> None:
        self.sent.append(request)


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def hold_registry():
    return ApprovalHoldRegistry()


@pytest.fixture
def event_bus():
    bus = MagicMock()
    bus.published = []

    def capture(event):
        bus.published.append(event)

    bus.publish = capture
    return bus


@pytest.fixture
def delivery():
    return FakeDelivery()


@pytest.fixture
def service(repo, hold_registry, event_bus, delivery):
    return ApprovalGateService(
        repository=repo,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )


class TestApprovalGateServiceCheck:

    async def test_check_tool_not_on_approval_list_returns_not_required(self, service):
        """Tool not requiring approval returns immediately."""
        policy = ToolAccessPolicy(allow_list=("search",))
        result = await service.check(mcp_server_id="notion", tool_name="search",
        arguments={},
        policy=policy,
        correlation_id="corr-1",)
        assert result.approved is True
        assert result.approval_id is None

    async def test_check_tool_on_approval_list_approve(self, service, hold_registry, event_bus):
        """Tool on approval_list blocks until approved."""
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=5,
        )

        async def approve_later():
            await asyncio.sleep(0.05)
            await service.resolve("dummy", True, "admin@test.com")

        # We need to resolve with the actual approval_id, but we don't know it yet.
        # So we intercept via the hold_registry.
        original_register = hold_registry.register

        captured_id = None

        async def capturing_register(approval_id):
            nonlocal captured_id
            captured_id = approval_id
            await original_register(approval_id)

        hold_registry.register = capturing_register

        async def approve_after_register():
            while captured_id is None:
                await asyncio.sleep(0.01)
            await service.resolve(captured_id, True, "admin@test.com")

        asyncio.create_task(approve_after_register())

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc"},
        policy=policy,
        correlation_id="corr-2",)
        assert result.approved is True
        assert result.approval_id is not None

        # Verify events published
        event_types = [type(e).__name__ for e in event_bus.published]
        assert "ToolApprovalRequested" in event_types
        assert "ToolApprovalGranted" in event_types

    async def test_check_tool_on_approval_list_deny(self, service, hold_registry, event_bus):
        """Tool on approval_list blocks until denied."""
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=5,
        )

        captured_id = None
        original_register = hold_registry.register

        async def capturing_register(approval_id):
            nonlocal captured_id
            captured_id = approval_id
            await original_register(approval_id)

        hold_registry.register = capturing_register

        async def deny_after_register():
            while captured_id is None:
                await asyncio.sleep(0.01)
            await service.resolve(captured_id, False, "admin@test.com", "Too risky")

        asyncio.create_task(deny_after_register())

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={},
        policy=policy,
        correlation_id="corr-3",)
        assert result.approved is False
        assert result.error_code == "approval_denied"

        event_types = [type(e).__name__ for e in event_bus.published]
        assert "ToolApprovalRequested" in event_types
        assert "ToolApprovalDenied" in event_types

    async def test_check_tool_on_approval_list_timeout(self, service, event_bus):
        """Tool on approval_list times out -> returns expired."""
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=0,  # immediate timeout
        )

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={},
        policy=policy,
        correlation_id="corr-4",)
        assert result.approved is False
        assert result.error_code == "approval_timeout"

        event_types = [type(e).__name__ for e in event_bus.published]
        assert "ToolApprovalRequested" in event_types
        assert "ToolApprovalExpired" in event_types

    async def test_check_sanitizes_sensitive_arguments(self, service, repo):
        """Sensitive argument keys are redacted before persistence."""
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=0,
        )

        await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc", "api_token": "secret123"},
        policy=policy,
        correlation_id="corr-5",)

        # Check persisted request has sanitized args
        requests = list(repo._store.values())
        assert len(requests) == 1
        assert requests[0].arguments["api_token"] == "[REDACTED]"
        assert requests[0].arguments["page_id"] == "abc"

    async def test_delivery_called(self, service, delivery):
        """Delivery.send() is called with the request."""
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=0,
        )

        await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={},
        policy=policy,
        correlation_id="corr-6",)

        assert len(delivery.sent) == 1
        assert delivery.sent[0].tool_name == "update_page"


class TestApprovalGateServiceResolve:

    async def test_resolve_nonexistent_returns_false(self, service):
        result = await service.resolve("nonexistent", True, "admin@test.com")
        assert result is False

    async def test_resolve_already_terminal_returns_false(self, service, repo):
        """Resolving an already-terminal request returns False."""
        now = datetime.now(timezone.utc)
        req = ApprovalRequest(
            approval_id="done-001",
            mcp_server_id="notion",
            tool_name="update_page",
            arguments={},
            arguments_hash="abc",
            requested_at=now,
            expires_at=now + timedelta(seconds=300),
            state=ApprovalState.APPROVED,
            channel="dashboard",
            decided_by="someone",
            decided_at=now,
        )
        await repo.save(req)

        result = await service.resolve("done-001", True, "admin@test.com")
        assert result is False
