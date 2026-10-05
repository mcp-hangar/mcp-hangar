"""An auth mutation is recorded against the caller, on the app ``serve --http`` builds (#1649).

The auth routes took ``assigned_by`` / ``created_by`` / ``revoked_by`` /
``updated_by`` from the request body, defaulting to ``"system"``. An admin key
could grant a role and have the ``RoleAssigned`` event, the ``assigning_role``
log line and the response all name somebody else.

The stack is the real one: an API key minted in the real SQLite key store, its
``admin`` role applied from config by ``bootstrap_auth``, the auth command
handlers on a real command bus, and ``create_api_router`` mounted at ``/api``
inside ``create_auth_enforced_app`` -- the assembly
``test_rest_authz_on_served_app.py`` mirrors from ``lifecycle.py``. The actor is
read off the auth context the served stack attaches, so that is what must be
under test: a helper reading the wrong shape would record ``anonymous`` and every
mock-request unit test would stay green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.testclient import TestClient
from structlog.testing import capture_logs

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.commands.handlers import register_auth_command_handlers
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.domain.events.auth import RoleAssigned, RoleRevoked
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import reset_event_bus
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app
from mcp_hangar.server.context import init_context, reset_context

CALLER = "svc:root"
FORGED = "user:someone-else"


@pytest.fixture(autouse=True)
def _clean_globals():
    reset_event_bus()
    reset_context()
    yield
    reset_context()
    reset_event_bus()


@pytest.fixture
def served(tmp_path: Path):
    """``(client, key, events, role_store, api_key_store)`` over the served stack."""
    events: list[object] = []
    components = bootstrap_auth(
        AuthConfig(
            enabled=True,
            allow_anonymous=False,
            api_key=ApiKeyAuthConfig(enabled=True),
            storage=StorageConfig(driver="sqlite", path=str(tmp_path / "auth.db")),
            role_assignments=[RoleAssignment(principal=CALLER, role="admin", scope="global")],
        ),
        event_publisher=events.append,
    )
    key = components.api_key_store.create_key(principal_id=CALLER, name="root")

    runtime = create_runtime(command_bus=CommandBus())
    register_auth_command_handlers(
        runtime.command_bus,
        api_key_store=components.api_key_store,
        role_store=components.role_store,
        event_bus=runtime.event_bus,
    )
    init_context(runtime).auth_components = components

    aux_app = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))])

    async def mcp_app(scope, receive, send):
        await JSONResponse({"surface": "mcp"})(scope, receive, send)

    async def combined_app(scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope.get("path", "").startswith("/api"):
            await aux_app(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    client = TestClient(create_auth_enforced_app(combined_app, components), raise_server_exceptions=False)
    events.clear()
    return client, {"X-API-Key": key}, events, components.role_store, components.api_key_store


def test_a_role_assignment_is_recorded_against_the_caller(served):
    client, headers, events, role_store, _ = served

    with capture_logs() as logs:
        response = client.post(
            "/api/auth/roles/assign",
            headers=headers,
            json={"principal_id": "user:bob", "role_name": "developer"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["assigned_by"] == CALLER
    [event] = [e for e in events if isinstance(e, RoleAssigned)]
    assert (event.principal_id, event.role_name, event.assigned_by) == ("user:bob", "developer", CALLER)
    [line] = [entry for entry in logs if entry["event"] == "assigning_role"]
    assert line["assigned_by"] == CALLER
    assert [r.name for r in role_store.get_roles_for_principal("user:bob")] == ["developer"]


def test_a_forged_assigner_is_refused_and_nothing_is_assigned(served):
    client, headers, events, role_store, _ = served

    response = client.post(
        "/api/auth/roles/assign",
        headers=headers,
        json={"principal_id": "user:bob", "role_name": "admin", "assigned_by": FORGED},
    )

    assert response.status_code == 422, response.text
    assert "assigned_by" in response.text
    assert FORGED not in str(events)
    assert role_store.get_roles_for_principal("user:bob") == []


def test_a_role_revocation_is_recorded_against_the_caller(served):
    client, headers, events, _, _ = served
    client.post("/api/auth/roles/assign", headers=headers, json={"principal_id": "user:bob", "role_name": "viewer"})

    forged = client.request(
        "DELETE",
        "/api/auth/roles/revoke",
        headers=headers,
        json={"principal_id": "user:bob", "role_name": "viewer", "revoked_by": FORGED},
    )
    response = client.request(
        "DELETE", "/api/auth/roles/revoke", headers=headers, json={"principal_id": "user:bob", "role_name": "viewer"}
    )

    assert forged.status_code == 422, forged.text
    assert response.status_code == 200, response.text
    assert response.json()["revoked_by"] == CALLER
    [event] = [e for e in events if isinstance(e, RoleRevoked)]
    assert event.revoked_by == CALLER


def test_a_key_is_created_and_revoked_by_the_caller(served):
    client, headers, _, _, api_key_store = served

    forged = client.post(
        "/api/auth/keys", headers=headers, json={"principal_id": "svc:ci", "name": "ci", "created_by": FORGED}
    )
    created = client.post("/api/auth/keys", headers=headers, json={"principal_id": "svc:ci", "name": "ci"})

    assert forged.status_code == 422, forged.text
    assert created.status_code == 201, created.text
    assert created.json()["created_by"] == CALLER
    key_id = created.json()["key_id"]

    forged_revoke = client.request("DELETE", f"/api/auth/keys/{key_id}", headers=headers, json={"revoked_by": FORGED})
    revoked = client.request("DELETE", f"/api/auth/keys/{key_id}", headers=headers)

    assert forged_revoke.status_code == 422, forged_revoke.text
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_by"] == CALLER


def test_a_custom_role_is_created_and_updated_by_the_caller(served):
    client, headers, _, _, _ = served

    forged = client.post("/api/auth/roles", headers=headers, json={"role_name": "ops", "created_by": FORGED})
    created = client.post("/api/auth/roles", headers=headers, json={"role_name": "ops"})
    forged_update = client.patch(
        "/api/auth/roles/ops", headers=headers, json={"permissions": ["tool:invoke:*"], "updated_by": FORGED}
    )
    updated = client.patch("/api/auth/roles/ops", headers=headers, json={"permissions": ["tool:invoke:*"]})

    assert forged.status_code == 422, forged.text
    assert created.status_code == 201, created.text
    assert created.json()["created_by"] == CALLER
    assert forged_update.status_code == 422, forged_update.text
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated_by"] == CALLER
