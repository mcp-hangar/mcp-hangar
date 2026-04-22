"""Integration tests for the approval gate end-to-end flow.

Covers the full lifecycle: policy -> gate service -> hold -> resolve -> result,
including timeout and deny-on-deny_list bypass scenarios.
"""

import asyncio

import pytest

from mcp_hangar.domain.events import (
    ToolApprovalDenied,
    ToolApprovalExpired,
    ToolApprovalGranted,
    ToolApprovalRequested,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

from enterprise.approvals.delivery.noop import NoOpApprovalDelivery
from enterprise.approvals.hold_registry import ApprovalHoldRegistry
from enterprise.approvals.models import ApprovalRequest, ApprovalResult, ApprovalState


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------


class FakeRepository:
    """In-memory repository for integration tests."""

    def __init__(self):
        self._store: dict[str, ApprovalRequest] = {}

    async def save(self, request):
        self._store[request.approval_id] = request

    async def get(self, approval_id):
        return self._store.get(approval_id)

    async def list_pending(self, mcp_server_id=None):
        return [
            r
            for r in self._store.values()
            if r.state == ApprovalState.PENDING
            and (mcp_server_id is None or r.mcp_server_id == mcp_server_id)
        ]

    async def list_by_state(self, state, mcp_server_id=None):
        return [
            r
            for r in self._store.values()
            if r.state == state
            and (mcp_server_id is None or r.mcp_server_id == mcp_server_id)
        ]

    async def update_state(self, approval_id, state, decided_by, decided_at, reason):
        r = self._store.get(approval_id)
        if r:
            r.state = state
            r.decided_by = decided_by
            r.decided_at = decided_at
            r.reason = reason


class FakeEventBus:
    """Captures published events for assertion."""

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def events_of_type(self, event_type):
        return [e for e in self.events if isinstance(e, event_type)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repository():
    return FakeRepository()


@pytest.fixture
def event_bus():
    return FakeEventBus()


@pytest.fixture
def hold_registry():
    return ApprovalHoldRegistry()


@pytest.fixture
def delivery():
    return NoOpApprovalDelivery()


@pytest.fixture
def gate_service(repository, hold_registry, event_bus, delivery):
    from enterprise.approvals.service import ApprovalGateService

    return ApprovalGateService(
        repository=repository,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApprovalFlowApprove:
    """Full approve flow: check -> hold -> resolve(approve) -> granted."""

    async def test_approve_flow_returns_granted(
        self, gate_service, hold_registry, event_bus, repository
    ):
        policy = ToolAccessPolicy(approval_list=("delete_*",))

        async def resolve_after_delay():
            await asyncio.sleep(0.05)
            pending = await repository.list_pending()
            assert len(pending) == 1
            approval_id = pending[0].approval_id
            await gate_service.resolve(approval_id, approved=True, decided_by="admin@test")

        resolve_task = asyncio.create_task(resolve_after_delay())

        result = await gate_service.check(
            mcp_server_id="grafana",
            tool_name="delete_dashboard",
            arguments={"id": "123"},
            policy=policy,
            correlation_id="corr-1",
        )

        await resolve_task

        assert result.approved is True
        assert result.approval_id is not None

        # Verify events
        requested = event_bus.events_of_type(ToolApprovalRequested)
        assert len(requested) == 1
        assert requested[0].tool_name == "delete_dashboard"

        granted = event_bus.events_of_type(ToolApprovalGranted)
        assert len(granted) == 1
        assert granted[0].decided_by == "admin@test"

    async def test_approve_flow_persists_state(
        self, gate_service, hold_registry, event_bus, repository
    ):
        policy = ToolAccessPolicy(approval_list=("run_query",))

        async def resolve_after_delay():
            await asyncio.sleep(0.05)
            pending = await repository.list_pending()
            approval_id = pending[0].approval_id
            await gate_service.resolve(approval_id, approved=True, decided_by="ops")

        resolve_task = asyncio.create_task(resolve_after_delay())

        await gate_service.check(
            mcp_server_id="db",
            tool_name="run_query",
            arguments={"sql": "SELECT 1"},
            policy=policy,
            correlation_id="corr-2",
        )

        await resolve_task

        # Check DB state
        approved = await repository.list_by_state(ApprovalState.APPROVED)
        assert len(approved) == 1
        assert approved[0].decided_by == "ops"


class TestApprovalFlowDeny:
    """Full deny flow: check -> hold -> resolve(deny) -> denied."""

    async def test_deny_flow_returns_denied_with_reason(
        self, gate_service, hold_registry, event_bus, repository
    ):
        policy = ToolAccessPolicy(approval_list=("deploy_*",))

        async def resolve_after_delay():
            await asyncio.sleep(0.05)
            pending = await repository.list_pending()
            approval_id = pending[0].approval_id
            await gate_service.resolve(
                approval_id, approved=False, decided_by="sec@test", reason="Too risky"
            )

        resolve_task = asyncio.create_task(resolve_after_delay())

        result = await gate_service.check(
            mcp_server_id="k8s",
            tool_name="deploy_service",
            arguments={"image": "app:latest"},
            policy=policy,
            correlation_id="corr-3",
        )

        await resolve_task

        assert result.approved is False
        assert result.error_code == "approval_denied"
        assert result.reason == "Too risky"

        denied = event_bus.events_of_type(ToolApprovalDenied)
        assert len(denied) == 1
        assert denied[0].reason == "Too risky"


class TestApprovalFlowTimeout:
    """Timeout flow: check -> hold -> timeout -> expired."""

    async def test_timeout_returns_expired(
        self, gate_service, hold_registry, event_bus
    ):
        policy = ToolAccessPolicy(
            approval_list=("dangerous_*",),
            approval_timeout_seconds=1,
        )

        result = await gate_service.check(
            mcp_server_id="admin",
            tool_name="dangerous_reset",
            arguments={},
            policy=policy,
            correlation_id="corr-4",
        )

        assert result.approved is False
        assert result.error_code == "approval_timeout"

        expired = event_bus.events_of_type(ToolApprovalExpired)
        assert len(expired) == 1


class TestApprovalFlowBypass:
    """Bypass cases: no approval needed, deny_list override."""

    async def test_no_approval_needed_returns_immediately(
        self, gate_service, event_bus
    ):
        policy = ToolAccessPolicy(approval_list=("delete_*",))

        result = await gate_service.check(
            mcp_server_id="grafana",
            tool_name="get_dashboard",
            arguments={},
            policy=policy,
            correlation_id="corr-5",
        )

        assert result.approved is True
        assert result.approval_id is None
        assert len(event_bus.events) == 0

    async def test_deny_list_takes_precedence_over_approval(
        self, gate_service, event_bus
    ):
        policy = ToolAccessPolicy(
            deny_list=("delete_*",),
            approval_list=("delete_*",),
        )

        result = await gate_service.check(
            mcp_server_id="grafana",
            tool_name="delete_dashboard",
            arguments={},
            policy=policy,
            correlation_id="corr-6",
        )

        # deny_list means tool is not even allowed, no approval needed
        assert result.approved is True
        assert result.approval_id is None
        assert len(event_bus.events) == 0


class TestApprovalFlowSanitization:
    """Verify sensitive arguments are sanitized before persistence."""

    async def test_sensitive_args_are_redacted(
        self, gate_service, hold_registry, repository
    ):
        policy = ToolAccessPolicy(
            approval_list=("connect_*",),
            approval_timeout_seconds=1,
        )

        await gate_service.check(
            mcp_server_id="db",
            tool_name="connect_database",
            arguments={
                "host": "localhost",
                "password": "s3cr3t",
                "api_token": "tok_abc",
            },
            policy=policy,
            correlation_id="corr-7",
        )

        # Check persisted arguments are sanitized
        pending = await repository.list_by_state(ApprovalState.EXPIRED)
        assert len(pending) == 1
        args = pending[0].arguments
        assert args["host"] == "localhost"
        assert args["password"] == "[REDACTED]"
        assert args["api_token"] == "[REDACTED]"


class TestApprovalFlowConcurrency:
    """Multiple concurrent approval flows should not interfere."""

    async def test_concurrent_approvals(
        self, gate_service, hold_registry, repository
    ):
        policy = ToolAccessPolicy(
            approval_list=("action_*",),
            approval_timeout_seconds=5,
        )

        async def check_and_resolve(tool_name, corr_id, approve):
            async def resolve():
                await asyncio.sleep(0.05)
                pending = await repository.list_pending()
                for req in pending:
                    if req.tool_name == tool_name:
                        await gate_service.resolve(
                            req.approval_id,
                            approved=approve,
                            decided_by="admin",
                        )
                        return

            task = asyncio.create_task(resolve())
            result = await gate_service.check(
                mcp_server_id="svc",
                tool_name=tool_name,
                arguments={"data": corr_id},
                policy=policy,
                correlation_id=corr_id,
            )
            await task
            return result

        results = await asyncio.gather(
            check_and_resolve("action_a", "c1", True),
            check_and_resolve("action_b", "c2", False),
        )

        assert results[0].approved is True
        assert results[1].approved is False
