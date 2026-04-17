# pyright: reportAny=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportImplicitOverride=false

"""Tests for the agent policy push endpoint authorization."""

from types import SimpleNamespace
from unittest.mock import Mock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.testclient import TestClient

from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.api.router import create_api_router


def _policy_payload() -> dict[str, object]:
    return {
        "version": 1,
        "tool_policies": [
            {
                "provider_id": "*",
                "tool_name": "power",
                "action": "require_approval",
                "approval_timeout_seconds": 300,
            }
        ],
    }


def _principal(principal_id: str = "service:agent") -> Principal:
    return Principal(id=PrincipalId(principal_id), type=PrincipalType.SERVICE_ACCOUNT)


def _client_with_auth(principal: Principal) -> TestClient:
    mock_auth = SimpleNamespace(principal=principal)

    class InjectAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.auth = mock_auth
            return await call_next(request)

    app = create_api_router()
    app.add_middleware(InjectAuthMiddleware)
    return TestClient(app, base_url="http://localhost")


class TestAgentPolicyEndpoint:
    def test_authorized_push_returns_200(self, monkeypatch) -> None:
        resolver = Mock()
        event_bus = Mock()
        authz_middleware = Mock()
        context = SimpleNamespace(auth_components=SimpleNamespace(authz_middleware=authz_middleware))

        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_tool_access_resolver", lambda: resolver)
        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_context", lambda: context)
        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_event_bus", lambda: event_bus)

        client = _client_with_auth(_principal())
        response = client.post("/agent/policy/", json=_policy_payload())

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": 1, "applied": 1}
        authz_middleware.authorize.assert_called_once_with(
            principal=_principal(),
            action="write",
            resource_type="policy",
            resource_id="*",
        )
        resolver.set_provider_policy.assert_called_once()
        event_bus.publish.assert_not_called()

    def test_missing_auth_returns_401(self, monkeypatch) -> None:
        event_bus = Mock()
        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_event_bus", lambda: event_bus)

        client = TestClient(create_api_router(), base_url="http://localhost")
        response = client.post("/agent/policy/", json=_policy_payload())

        assert response.status_code == 401
        published_event = event_bus.publish.call_args.args[0]
        assert published_event.principal_id == "anonymous"
        assert published_event.reason == "authentication_required"

    def test_wrong_permission_returns_403(self, monkeypatch) -> None:
        resolver = Mock()
        event_bus = Mock()
        authz_middleware = Mock()
        authz_middleware.authorize.side_effect = AccessDeniedError(
            principal_id="service:agent",
            action="write",
            resource="policy:*",
            reason="no_matching_permission",
        )
        context = SimpleNamespace(auth_components=SimpleNamespace(authz_middleware=authz_middleware))

        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_tool_access_resolver", lambda: resolver)
        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_context", lambda: context)
        monkeypatch.setattr("mcp_hangar.server.api.agent_policy.get_event_bus", lambda: event_bus)

        client = _client_with_auth(_principal())
        response = client.post("/agent/policy/", json=_policy_payload())

        assert response.status_code == 403
        resolver.set_provider_policy.assert_not_called()
        published_event = event_bus.publish.call_args.args[0]
        assert published_event.principal_id == "service:agent"
        assert published_event.reason == "policy_write_permission_required"
