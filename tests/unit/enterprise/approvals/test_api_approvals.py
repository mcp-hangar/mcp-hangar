"""Unit tests for approval API routes."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from enterprise.approvals.api.routes import approval_routes
from enterprise.approvals.hold_registry import ApprovalHoldRegistry
from enterprise.approvals.models import ApprovalRequest, ApprovalState
from enterprise.approvals.service import ApprovalGateService


class FakeRepository:
    def __init__(self):
        self._store: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self._store[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._store.get(approval_id)

    async def list_pending(self, mcp_server_id=None):
        return await self.list_by_state(ApprovalState.PENDING, mcp_server_id)

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
    async def send(self, request):
        pass


def _make_pending_request(approval_id="test-001", **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        approval_id=approval_id,
        mcp_server_id="notion",
        tool_name="update_page",
        arguments={"page_id": "abc"},
        arguments_hash="sha256:test",
        requested_at=now,
        expires_at=now + timedelta(seconds=300),
        state=ApprovalState.PENDING,
        channel="dashboard",
    )
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


@pytest.fixture
def app_with_service():
    repo = FakeRepository()
    hold_registry = ApprovalHoldRegistry()
    event_bus = MagicMock()
    event_bus.publish = MagicMock()
    delivery = FakeDelivery()

    service = ApprovalGateService(
        repository=repo,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )

    app = Starlette(routes=approval_routes)
    app.state.approval_gate_service = service
    app.state.slack_signing_secret = "test-secret"

    return app, service, repo


@pytest.fixture
def client(app_with_service):
    app, _, _ = app_with_service
    return TestClient(app)


class TestListApprovals:

    def test_list_pending_returns_only_pending(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        # Add pending and approved requests
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(_make_pending_request("p-001")))
        loop.run_until_complete(repo.save(
            _make_pending_request("a-001", state=ApprovalState.APPROVED)
        ))
        loop.close()

        response = client.get("/enterprise/approvals")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["approval_id"] == "p-001"

    def test_list_approved_by_state(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(
            _make_pending_request("a-001", state=ApprovalState.APPROVED)
        ))
        loop.close()

        response = client.get("/enterprise/approvals?state=approved")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["state"] == "approved"

    def test_invalid_state_returns_400(self, client):
        response = client.get("/enterprise/approvals?state=invalid")
        assert response.status_code == 400


class TestGetApproval:

    def test_get_existing_approval(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(_make_pending_request("test-001")))
        loop.close()

        response = client.get("/enterprise/approvals/test-001")
        assert response.status_code == 200
        data = response.json()
        assert data["approval_id"] == "test-001"
        assert data["tool_name"] == "update_page"

    def test_get_nonexistent_returns_404(self, client):
        response = client.get("/enterprise/approvals/nonexistent")
        assert response.status_code == 404


class TestResolveApproval:

    def test_resolve_approve_success(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        req = _make_pending_request("r-001")
        loop.run_until_complete(repo.save(req))
        loop.run_until_complete(service._hold_registry.register("r-001"))
        loop.close()

        response = client.post(
            "/enterprise/approvals/r-001/resolve",
            json={"decision": "approve"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approval_id"] == "r-001"

    def test_resolve_deny_success(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        req = _make_pending_request("r-002")
        loop.run_until_complete(repo.save(req))
        loop.run_until_complete(service._hold_registry.register("r-002"))
        loop.close()

        response = client.post(
            "/enterprise/approvals/r-002/resolve",
            json={"decision": "deny", "reason": "Too risky"},
        )
        assert response.status_code == 200

    def test_resolve_nonexistent_returns_404(self, client):
        response = client.post(
            "/enterprise/approvals/nonexistent/resolve",
            json={"decision": "approve"},
        )
        assert response.status_code == 404

    def test_resolve_already_resolved_returns_409(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(
            _make_pending_request("done-001", state=ApprovalState.APPROVED)
        ))
        loop.close()

        response = client.post(
            "/enterprise/approvals/done-001/resolve",
            json={"decision": "approve"},
        )
        assert response.status_code == 409

    def test_resolve_invalid_decision_returns_400(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(_make_pending_request("r-003")))
        loop.close()

        response = client.post(
            "/enterprise/approvals/r-003/resolve",
            json={"decision": "maybe"},
        )
        assert response.status_code == 400


class TestSlackCallback:

    def _make_slack_request(self, body: str, secret: str, timestamp: int | None = None):
        """Generate Slack-compatible HMAC headers."""
        ts = timestamp or int(time.time())
        sig_basestring = f"v0:{ts}:{body}"
        sig = "v0=" + hmac.new(
            secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "x-slack-signature": sig,
            "x-slack-request-timestamp": str(ts),
        }

    def test_slack_stale_timestamp_returns_401(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(_make_pending_request("s-001")))
        loop.close()

        stale_ts = int(time.time()) - 600
        body = "payload=" + json.dumps({"actions": [{"action_id": "approve_s-001"}], "user": {"id": "U123"}})
        headers = self._make_slack_request(body, "test-secret", stale_ts)

        response = client.post(
            "/enterprise/approvals/s-001/resolve",
            content=body,
            headers=headers,
        )
        assert response.status_code == 401

    def test_slack_invalid_signature_returns_401(self, app_with_service):
        app, service, repo = app_with_service
        client = TestClient(app)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(repo.save(_make_pending_request("s-002")))
        loop.close()

        headers = {
            "x-slack-signature": "v0=invalidsig",
            "x-slack-request-timestamp": str(int(time.time())),
        }

        response = client.post(
            "/enterprise/approvals/s-002/resolve",
            content="payload={}",
            headers=headers,
        )
        assert response.status_code == 401
