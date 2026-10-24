"""A body that is not a JSON object, or lacks a field the route indexes, is a 400 (#2066).

`await request.json()` raised on invalid JSON, and a list or string body then
failed on `.get` or an index; each answered a generic 500 and was logged as an
unhandled fault for what was the caller's mistake.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.testclient import TestClient

ROUTES = [
    ("POST", "/mcp_servers/"),
    ("PATCH", "/mcp_servers/srv"),
    ("POST", "/mcp_servers/srv/l7_policy"),
    ("POST", "/groups/"),
    ("PUT", "/groups/g"),
    ("POST", "/groups/g/members"),
    ("POST", "/discovery/sources"),
    ("PUT", "/discovery/sources/s"),
    ("PUT", "/discovery/sources/s/enable"),
    ("POST", "/auth/keys"),
    ("POST", "/auth/roles/assign"),
    ("DELETE", "/auth/roles/revoke"),
    ("POST", "/auth/roles"),
    ("PATCH", "/auth/roles/custom"),
    ("POST", "/auth/check-permission"),
    ("POST", "/auth/policies/provider/srv"),
    ("POST", "/approvals/abc/resolve"),
]
BODIES = ["notjson", "[]", '"x"', "{"]


@pytest.fixture
def client():
    from mcp_hangar.server.api import create_api_router

    ctx = Mock()
    ctx.auth_components = None
    with (
        patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.middleware.dispatch_command", new=AsyncMock(return_value={})),
        patch("mcp_hangar.server.api.middleware.dispatch_query", new=AsyncMock(return_value={})),
        # Reached past the "not configured" answers, so the body is what is judged.
        patch("mcp_hangar.server.api.discovery._require_orchestrator", return_value=Mock()),
        patch("mcp_hangar.approvals.api.routes._get_approval_service", return_value=Mock()),
    ):
        yield TestClient(create_api_router(auth_components=None), raise_server_exceptions=False)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_it_is_never_a_500(client, method, path, body) -> None:
    reply = client.request(method, path, content=body, headers={"Content-Type": "application/json"})

    assert reply.status_code == 400, (method, path, body, reply.status_code, reply.text)
    assert reply.json()["error"] == "invalid_body"


@pytest.mark.parametrize(
    ("method", "path", "body", "field"),
    [
        ("POST", "/auth/keys", {"principal_id": "svc:x"}, "name"),
        ("POST", "/auth/roles/assign", {"principal_id": "svc:x"}, "role_name"),
        ("POST", "/auth/roles", {"description": "d"}, "role_name"),
    ],
)
def test_a_missing_field_is_named(client, method, path, body, field) -> None:
    reply = client.request(method, path, json=body)

    assert reply.status_code == 400, reply.text
    assert reply.json()["error"] == "missing_fields" and field in reply.json()["detail"]
