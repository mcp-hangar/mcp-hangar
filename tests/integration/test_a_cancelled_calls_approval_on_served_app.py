"""An approval for a held call whose batch was cancelled is refused on the served app (#1702).

The batch deadline passed while the call was held, so the call will not run.
Approving it afterwards used to answer 200, record `approved` and publish a
grant naming the approver -- an audit trail asserting that someone let through a
call that never ran. It now answers 409 and records `cancelled`.

Driven through the app ``serve --http`` builds, with real API-key auth and the
real RBAC authorizer (see test_tenant_scoped_grants_on_served_app.py); the hold
is registered the way the gate registers it, with the batch's cancel event.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.testclient import TestClient

from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.security import Permission, Role
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from tests.integration.test_tenant_scoped_grants_on_served_app import (
    _ApprovalRepository,
    _bootstrap,
    _Events,
    _fresh_state,  # noqa: F401 -- autouse fixture, applied by import
    _pending,
    _served_app,
    _wire_context,
)


@pytest.fixture()
def stack(tmp_path):
    components, keys = _bootstrap(tmp_path, {})
    components.role_store.add_role(
        Role(
            name="approver",
            permissions=frozenset({Permission("approval", "read"), Permission("approval", "resolve")}),
        )
    )
    components.role_store.assign_role("svc:approver", "approver", scope="global")
    keys["approver"] = components.api_key_store.create_key(principal_id="svc:approver", name="k", tenant_id=None)

    repo = _ApprovalRepository()
    holds = ApprovalHoldRegistry()
    abandoned = threading.Event()
    for approval_id in ("ap-held", "ap-live"):
        repo.store[approval_id] = _pending(approval_id, None)
    # Both held here; only ap-held's batch has been cancelled.
    asyncio.run(holds.register("ap-held", abandoned=abandoned))
    asyncio.run(holds.register("ap-live", abandoned=threading.Event()))
    abandoned.set()

    service = ApprovalGateService(
        repository=repo, hold_registry=holds, event_bus=_Events(), delivery=NoOpApprovalDelivery()
    )
    _wire_context(components, InMemoryEventStore()).approval_gate = service
    return TestClient(_served_app(components), raise_server_exceptions=False), keys["approver"], repo, holds


def _resolve(client: TestClient, key: str, approval_id: str, decision: str = "approve"):
    return client.post(f"/api/approvals/{approval_id}/resolve", headers={"X-API-Key": key}, json={"decision": decision})


def test_approving_a_cancelled_call_answers_409_and_records_cancelled(stack) -> None:
    client, key, repo, holds = stack

    response = _resolve(client, key, "ap-held")

    assert response.status_code == 409, response.text
    assert response.json() == {
        "error": "Approval refused: the held call was cancelled and did not run",
        "state": "cancelled",
    }
    assert repo.store["ap-held"].state is ApprovalState.CANCELLED
    # The hold is released with a refusal, never a grant.
    assert asyncio.run(holds.wait_slice("ap-held", 0)) is False

    listed = client.get("/api/approvals?state=cancelled", headers={"X-API-Key": key})
    assert listed.status_code == 200, listed.text
    assert [a["approval_id"] for a in listed.json()] == ["ap-held"]

    again = _resolve(client, key, "ap-held")
    assert (again.status_code, again.json()["state"]) == (409, "cancelled"), again.text


def test_denying_a_cancelled_call_is_still_a_denial(stack) -> None:
    """A denial grants nothing, so it is recorded as before (#1541)."""
    client, key, repo, _holds = stack

    response = _resolve(client, key, "ap-held", decision="deny")

    assert response.status_code == 200, response.text
    assert repo.store["ap-held"].state is ApprovalState.DENIED


def test_approving_a_call_still_held_is_a_grant(stack) -> None:
    client, key, repo, holds = stack

    response = _resolve(client, key, "ap-live")

    assert response.status_code == 200, response.text
    assert repo.store["ap-live"].state is ApprovalState.APPROVED
    assert asyncio.run(holds.wait_slice("ap-live", 0)) is True
