"""A role removed from ``auth.role_assignments`` stops working after a restart, on the served app (#2052).

Two boots of the stack ``serve --http`` builds, on one SQLite auth database:
real API keys, roles applied from config by ``bootstrap_auth``, the auth
command and query handlers on real buses and the REST router behind
``create_auth_enforced_app`` (the assembly
``test_auth_actor_is_the_caller_on_the_served_app.py`` uses, with the
query handlers wired by ``register_auth_cqrs`` as ``serve`` wires them). Between the
boots two ``admin`` lines are deleted from the config. The principal that held
its role only through the file loses it; one the admin also granted through
``POST /api/auth/roles/assign`` keeps it, and so does a principal the admin
granted a role the file never listed.

Only names that exist before the fix are imported, so without it the test
fails on its assertions rather than at collection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import reset_event_bus
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app
from mcp_hangar.server.bootstrap.components import register_auth_cqrs
from mcp_hangar.server.context import init_context, reset_context

pytestmark = pytest.mark.security

ROOT = "svc:root"
REMOVED = "svc:removed-admin"
ALSO_API = "svc:also-api-admin"
API_ONLY = "user:bob"


@pytest.fixture(autouse=True)
def _clean_globals():
    reset_event_bus()
    reset_context()
    yield
    reset_context()
    reset_event_bus()


def _boot(db: Path, principals: list[str]):
    """Boot the served stack with an ``admin`` line per principal; return ``(client, components)``."""
    reset_event_bus()
    reset_context()
    components = bootstrap_auth(
        AuthConfig(
            enabled=True,
            allow_anonymous=False,
            api_key=ApiKeyAuthConfig(enabled=True),
            storage=StorageConfig(driver="sqlite", path=str(db)),
            role_assignments=[RoleAssignment(principal=p, role="admin", scope="global") for p in principals],
        )
    )
    runtime = create_runtime(command_bus=CommandBus(), query_bus=QueryBus())
    assert register_auth_cqrs(runtime, components)
    init_context(runtime).auth_components = components
    aux_app = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))])

    async def combined_app(scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope.get("path", "").startswith("/api"):
            await aux_app(scope, receive, send)
            return
        await JSONResponse({"surface": "mcp"})(scope, receive, send)

    client = TestClient(create_auth_enforced_app(combined_app, components), raise_server_exceptions=False)
    return client, components


def _held(client: TestClient, root: dict[str, str], principal: str) -> list[str]:
    response = client.get("/api/auth/principals/roles", headers=root, params={"principal_id": principal})
    assert response.status_code == 200, response.text
    return sorted(r["name"] for r in response.json()["roles"])


def test_a_grant_removed_from_the_file_is_revoked_and_api_grants_stay(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"

    client, components = _boot(db, [ROOT, REMOVED, ALSO_API])
    keys = {
        p: {"X-API-Key": components.api_key_store.create_key(principal_id=p, name=p)} for p in (ROOT, REMOVED, ALSO_API)
    }
    assert client.get("/api/auth/roles", headers=keys[REMOVED]).status_code == 200
    for principal, role in ((ALSO_API, "admin"), (API_ONLY, "developer")):
        response = client.post(
            "/api/auth/roles/assign", headers=keys[ROOT], json={"principal_id": principal, "role_name": role}
        )
        assert response.status_code == 200, response.text

    # Restart with both lines but root's deleted from the file.
    client, _ = _boot(db, [ROOT])

    assert client.get("/api/auth/roles", headers=keys[REMOVED]).status_code == 403
    assert _held(client, keys[ROOT], REMOVED) == []
    assert client.get("/api/auth/roles", headers=keys[ALSO_API]).status_code == 200
    assert _held(client, keys[ROOT], API_ONLY) == ["developer"]
    assert client.get("/api/auth/roles", headers=keys[ROOT]).status_code == 200
