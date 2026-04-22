"""Free vs Enterprise behavior scenarios.

Tests demonstrating that Free-tier (no enterprise features bootstrapped)
allows tool invocations to pass through immediately, while Enterprise-tier
(with approval gate configured) holds execution until a human decision.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from enterprise.approvals.hold_registry import ApprovalHoldRegistry
from enterprise.approvals.models import ApprovalState
from enterprise.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class FakeRepository:
    def __init__(self):
        self._store = {}

    async def save(self, request):
        self._store[request.approval_id] = request

    async def get(self, approval_id):
        return self._store.get(approval_id)

    async def list_pending(self, mcp_server_id=None):
        return [
            r for r in self._store.values()
            if r.state == ApprovalState.PENDING
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
    def __init__(self):
        self.sent = []

    async def send(self, request):
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


class TestFreePassesEnterpriseCatches:

    @pytest.mark.asyncio
    async def test_free_no_approval_list_passes_immediately(self, service):
        policy = ToolAccessPolicy(allow_list=("update_page",))

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc"},
        policy=policy,
        correlation_id="free-corr-1",)

        assert result.approved is True
        assert result.approval_id is None

    @pytest.mark.asyncio
    async def test_enterprise_approval_list_holds_execution(
        self, service, hold_registry, event_bus, delivery
    ):
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

        async def approve_after_register():
            while captured_id is None:
                await asyncio.sleep(0.01)
            await service.resolve(captured_id, True, "security-admin@corp.com")

        asyncio.create_task(approve_after_register())

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc"},
        policy=policy,
        correlation_id="enterprise-corr-1",)

        assert result.approved is True
        assert result.approval_id is not None

        assert len(delivery.sent) == 1
        assert delivery.sent[0].tool_name == "update_page"
        assert delivery.sent[0].mcp_server_id == "notion"

        event_types = [type(e).__name__ for e in event_bus.published]
        assert "ToolApprovalRequested" in event_types
        assert "ToolApprovalGranted" in event_types

    @pytest.mark.asyncio
    async def test_enterprise_approval_denied_blocks_execution(
        self, service, hold_registry, event_bus, delivery
    ):
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
            await service.resolve(captured_id, False, "security-admin@corp.com", "Policy violation")

        asyncio.create_task(deny_after_register())

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc"},
        policy=policy,
        correlation_id="enterprise-corr-2",)

        assert result.approved is False
        assert result.error_code == "approval_denied"
        assert result.approval_id is not None

        event_types = [type(e).__name__ for e in event_bus.published]
        assert "ToolApprovalRequested" in event_types
        assert "ToolApprovalDenied" in event_types

    @pytest.mark.asyncio
    async def test_enterprise_timeout_blocks_execution(self, service, event_bus, delivery):
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=0,
        )

        result = await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc"},
        policy=policy,
        correlation_id="enterprise-corr-3",)

        assert result.approved is False
        assert result.error_code == "approval_timeout"

        assert len(delivery.sent) == 1

        event_types = [type(e).__name__ for e in event_bus.published]
        assert "ToolApprovalRequested" in event_types
        assert "ToolApprovalExpired" in event_types

    @pytest.mark.asyncio
    async def test_free_tool_not_on_any_list_passes(self, service):
        policy = ToolAccessPolicy()

        result = await service.check(mcp_server_id="notion", tool_name="search",
        arguments={"query": "test"},
        policy=policy,
        correlation_id="free-corr-2",)

        assert result.approved is True
        assert result.approval_id is None

    @pytest.mark.asyncio
    async def test_enterprise_sensitive_args_redacted_before_hold(
        self, service, repo
    ):
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=0,
        )

        await service.check(mcp_server_id="notion", tool_name="update_page",
        arguments={"page_id": "abc", "api_token": "secret-value"},
        policy=policy,
        correlation_id="enterprise-corr-4",)

        requests = list(repo._store.values())
        assert len(requests) == 1
        assert requests[0].arguments["api_token"] == "[REDACTED]"
        assert requests[0].arguments["page_id"] == "abc"
