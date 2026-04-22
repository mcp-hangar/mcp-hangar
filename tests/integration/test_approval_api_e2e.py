"""Integration test for approval REST API with the full service stack.

Tests the complete HTTP -> route -> service -> hold -> resolve -> response path.
"""

import asyncio

import pytest
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

from enterprise.approvals.api.routes import approval_routes
from enterprise.approvals.delivery.noop import NoOpApprovalDelivery
from enterprise.approvals.hold_registry import ApprovalHoldRegistry
from enterprise.approvals.models import ApprovalState
from enterprise.approvals.service import ApprovalGateService


# ---------------------------------------------------------------------------
# In-memory repository (same as test_approval_flow.py)
# ---------------------------------------------------------------------------


class InMemoryRepository:
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

    async def list_by_state(self, state, mcp_server_id=None):
        return [
            r for r in self._store.values()
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
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service_stack():
    repo = InMemoryRepository()
    hold_registry = ApprovalHoldRegistry()
    event_bus = FakeEventBus()
    delivery = NoOpApprovalDelivery()
    service = ApprovalGateService(
        repository=repo,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )
    return repo, hold_registry, event_bus, service


@pytest.fixture
def client(service_stack):
    from starlette.applications import Starlette

    repo, hold_registry, event_bus, service = service_stack
    app = Starlette(routes=[Mount("/", routes=approval_routes)])
    app.state.approval_gate_service = service
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApprovalAPIListEmpty:
    def test_list_pending_empty(self, client):
        resp = client.get("/enterprise/approvals?state=pending")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_invalid_state(self, client):
        resp = client.get("/enterprise/approvals?state=invalid")
        assert resp.status_code == 400


class TestApprovalAPIResolveFlow:
    async def test_create_then_resolve_via_api(self, service_stack, client):
        repo, hold_registry, event_bus, service = service_stack
        policy = ToolAccessPolicy(approval_list=("delete_*",), approval_timeout_seconds=10)

        async def do_check():
            return await service.check(
                mcp_server_id="grafana",
                tool_name="delete_dashboard",
                arguments={"id": "42"},
                policy=policy,
                correlation_id="api-test",
            )

        # Start the check in a background task
        loop = asyncio.get_event_loop()
        check_task = loop.create_task(do_check())

        # Wait for the request to appear
        await asyncio.sleep(0.1)

        # List pending via API
        resp = client.get("/enterprise/approvals?state=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        approval_id = data[0]["approval_id"]
        assert data[0]["tool_name"] == "delete_dashboard"
        assert data[0]["state"] == "pending"
        assert data[0]["expires_in_seconds"] > 0

        # Get single approval via API
        resp = client.get(f"/enterprise/approvals/{approval_id}")
        assert resp.status_code == 200
        assert resp.json()["approval_id"] == approval_id

        # Resolve via API
        resp = client.post(
            f"/enterprise/approvals/{approval_id}/resolve",
            json={"decision": "approve"},
            headers={"x-principal-id": "api-user"},
        )
        assert resp.status_code == 200

        # Wait for check to complete
        result = await check_task
        assert result.approved is True

    async def test_resolve_unknown_returns_404(self, client):
        resp = client.post(
            "/enterprise/approvals/nonexistent/resolve",
            json={"decision": "approve"},
        )
        assert resp.status_code == 404

    async def test_resolve_invalid_decision_returns_400(self, service_stack, client):
        repo, hold_registry, event_bus, service = service_stack
        policy = ToolAccessPolicy(approval_list=("x_*",), approval_timeout_seconds=5)

        async def do_check():
            return await service.check(
                mcp_server_id="svc",
                tool_name="x_action",
                arguments={},
                policy=policy,
                correlation_id="bad-decision",
            )

        loop = asyncio.get_event_loop()
        check_task = loop.create_task(do_check())
        await asyncio.sleep(0.1)

        pending = client.get("/enterprise/approvals?state=pending").json()
        approval_id = pending[0]["approval_id"]

        resp = client.post(
            f"/enterprise/approvals/{approval_id}/resolve",
            json={"decision": "maybe"},
        )
        assert resp.status_code == 400

        # Clean up: resolve properly to unblock
        client.post(
            f"/enterprise/approvals/{approval_id}/resolve",
            json={"decision": "deny"},
        )
        await check_task

    async def test_double_resolve_returns_409(self, service_stack, client):
        repo, hold_registry, event_bus, service = service_stack
        policy = ToolAccessPolicy(approval_list=("y_*",), approval_timeout_seconds=10)

        async def do_check():
            return await service.check(
                mcp_server_id="svc",
                tool_name="y_action",
                arguments={},
                policy=policy,
                correlation_id="double-resolve",
            )

        loop = asyncio.get_event_loop()
        check_task = loop.create_task(do_check())
        await asyncio.sleep(0.1)

        pending = client.get("/enterprise/approvals?state=pending").json()
        approval_id = pending[0]["approval_id"]

        # First resolve
        resp1 = client.post(
            f"/enterprise/approvals/{approval_id}/resolve",
            json={"decision": "approve"},
        )
        assert resp1.status_code == 200

        await check_task

        # Second resolve should be 409
        resp2 = client.post(
            f"/enterprise/approvals/{approval_id}/resolve",
            json={"decision": "deny"},
        )
        assert resp2.status_code == 409
