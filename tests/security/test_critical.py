"""Security regression tests for critical attack paths."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportPrivateUsage=false, reportUnusedParameter=false

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.testclient import TestClient

from mcp_hangar.domain.exceptions import MissingCredentialsError
from mcp_hangar.server.api.middleware import get_cors_config
from mcp_hangar.server.api.router import create_api_router
from mcp_hangar.server.lifecycle import ServerLifecycle
from mcp_hangar.server.bootstrap import ApplicationContext


pytestmark = pytest.mark.security


def test_k1_agent_policy_rejects_unauthenticated_and_spoofed_header() -> None:
    client = TestClient(create_api_router(), base_url="http://localhost")
    payload: dict[str, object] = {"version": 1, "tool_policies": []}

    response = client.post("/agent/policy/", json=payload)
    assert response.status_code in {401, 403}

    spoofed = client.post(
        "/agent/policy/",
        json=payload,
        headers={"x-hangar-agent-internal": "true"},
    )
    assert spoofed.status_code in {401, 403}


def test_k2_websocket_without_valid_auth_is_rejected() -> None:
    mock_context = ApplicationContext(runtime=MagicMock(), mcp_server=MagicMock())
    lifecycle = ServerLifecycle(mock_context)

    auth_components = MagicMock()
    auth_components.authn_middleware.authenticate.side_effect = MissingCredentialsError("No credentials provided")

    inner_app: AsyncMock = AsyncMock()
    auth_app = cast(Any, lifecycle._create_auth_app(inner_app, auth_components))

    sent_messages: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    scope: dict[str, object] = {
        "type": "websocket",
        "path": "/api/ws/events",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "query_string": b"",
    }

    asyncio.run(auth_app(scope, AsyncMock(), send))

    inner_app.assert_not_called()
    assert sent_messages == [{"type": "websocket.close", "code": 1008, "reason": "No credentials provided"}]


def test_k3_non_loopback_http_without_auth_is_refused(monkeypatch) -> None:
    import uvicorn

    monkeypatch.setattr(
        uvicorn, "Config", MagicMock(side_effect=AssertionError("uvicorn.Config should not be reached"))
    )

    mock_context = ApplicationContext(runtime=MagicMock(), mcp_server=MagicMock(), auth_components=None)
    lifecycle = ServerLifecycle(mock_context)

    with pytest.raises(SystemExit):
        lifecycle.run_http(host="0.0.0.0", port=8000, unsafe_no_auth=False)


def test_k4_cors_and_host_hardening_are_strict() -> None:
    cors = get_cors_config()

    assert cors["allow_credentials"] is False
    assert "*" not in cors["allow_methods"]
    assert "*" not in cors["allow_headers"]

    app = create_api_router()
    assert any(middleware.cls is TrustedHostMiddleware for middleware in app.user_middleware)
