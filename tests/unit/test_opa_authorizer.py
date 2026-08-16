"""The OPA authorizer and the combined authorizer that fronts it."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, AuthorizationResult
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType


def _make_principal(
    subject: str = "user123",
    tenant_id: str | None = None,
    groups: frozenset[str] | None = None,
) -> Principal:
    return Principal(
        id=PrincipalId(subject),
        type=PrincipalType.USER,
        tenant_id=tenant_id,
        groups=groups or frozenset(),
    )


def _make_authz_request(
    principal: Principal | None = None,
    action: str = "invoke",
    resource_type: str = "tool",
    resource_id: str = "calculator",
    context: dict[str, Any] | None = None,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        principal=principal or _make_principal(),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        context=context or {},
    )


class TestOPAAuthorizerInit:
    """Test OPAAuthorizer initialization."""

    def test_trailing_slash_stripped_from_url(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181/")
        assert opa._opa_url == "http://localhost:8181"

    def test_leading_slash_stripped_from_policy_path(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181", policy_path="/v1/data/allow")
        assert opa._policy_path == "v1/data/allow"

    def test_timeout_set(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181", timeout=10.0)
        assert opa._timeout == 10.0

    def test_client_initially_none(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        assert opa._client is None


class TestOPAAuthorizerEvaluate:
    """Test OPAAuthorizer.evaluate with various error scenarios."""

    def test_httpx_not_installed_denies(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        with patch.dict("sys.modules", {"httpx": None}):
            result = opa.evaluate({"principal": {"id": "u1"}})
            assert not result.allowed
            assert "httpx_not_installed" in result.reason

    def test_successful_allow(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert result.allowed
        assert result.reason == "opa_policy"

    def test_successful_deny(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": False}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert result.reason == "opa_denied"

    def test_missing_result_key_defaults_to_deny(self):
        """OPA omits `result` when the queried rule is undefined.

        Still a denial, but reported as a configuration error rather than as
        `opa_denied`: an operator staring at a wrong policy_path needs to be
        able to tell "the policy said no" from "there is no policy here".
        """
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {}  # no "result" key
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert result.reason == "opa_error:undefined_result"

    def test_connect_error_denies(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "connection_failed" in result.reason

    def test_timeout_denies(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.TimeoutException("timed out")
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "timeout" in result.reason

    def test_http_status_error_denies(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_response)
        mock_client.post.side_effect = error
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "http_500" in result.reason

    def test_generic_exception_denies(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = RuntimeError("unexpected")
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "RuntimeError" in result.reason

    def test_lazy_client_initialization(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181", timeout=3.0)
        assert opa._client is None

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client_cls.return_value = mock_instance

            result = opa.evaluate({"principal": {"id": "u1"}})
            assert result.allowed
            mock_client_cls.assert_called_once_with(timeout=3.0)


class TestOPAAuthorizerBuildInput:
    """Test static build_input method."""

    def test_build_input_structure(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        principal = _make_principal(
            subject="user:alice",
            tenant_id="acme",
            groups=frozenset(["admin"]),
        )
        request = _make_authz_request(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="calc",
            context={"rate_limit": True},
        )

        input_data = OPAAuthorizer.build_input(request)

        assert input_data["principal"]["id"] == "user:alice"
        assert input_data["principal"]["type"] == "user"
        assert input_data["principal"]["tenant_id"] == "acme"
        assert "admin" in input_data["principal"]["groups"]
        assert input_data["action"] == "invoke"
        assert input_data["resource"]["type"] == "tool"
        assert input_data["resource"]["id"] == "calc"
        assert input_data["context"] == {"rate_limit": True}


class TestOPAAuthorizerAuthorize:
    """Test authorize convenience method."""

    def test_delegates_to_build_input_and_evaluate(self):
        import httpx

        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        request = _make_authz_request()
        result = opa.authorize(request)
        assert result.allowed


class TestOPAAuthorizerCloseAndContextManager:
    """Test close and context manager."""

    def test_close_when_client_exists(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        mock_client = MagicMock()
        opa._client = mock_client

        opa.close()
        mock_client.close.assert_called_once()
        assert opa._client is None

    def test_close_when_no_client(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        opa.close()  # Should not raise

    def test_context_manager(self):
        from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        mock_client = MagicMock()
        opa._client = mock_client

        with opa as ctx:
            assert ctx is opa

        mock_client.close.assert_called_once()


class TestCombinedAuthorizer:
    """Test CombinedAuthorizer dual strategy."""

    def _make_combined(self, require_both: bool = False, opa: Any = "auto"):
        from mcp_hangar.auth.infrastructure.opa_authorizer import CombinedAuthorizer, OPAAuthorizer
        from mcp_hangar.auth.infrastructure.rbac_authorizer import RBACAuthorizer

        rbac = MagicMock(spec=RBACAuthorizer)
        if opa == "auto":
            opa_auth = MagicMock(spec=OPAAuthorizer)
        elif opa is None:
            opa_auth = None
        else:
            opa_auth = opa
        return CombinedAuthorizer(rbac, opa_auth, require_both=require_both), rbac, opa_auth

    def test_no_opa_returns_rbac_result(self):
        combined, rbac, _ = self._make_combined(opa=None)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert result.reason == "rbac_ok"

    def test_require_both_false_rbac_allows_skips_opa(self):
        combined, rbac, opa = self._make_combined(require_both=False)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok", role="admin")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert result.reason == "rbac_ok"
        opa.authorize.assert_not_called()

    def test_require_both_false_rbac_denies_opa_allows(self):
        combined, rbac, opa = self._make_combined(require_both=False)
        rbac.authorize.return_value = AuthorizationResult.deny(reason="rbac_denied")
        opa.authorize.return_value = AuthorizationResult.allow(reason="opa_ok")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert result.reason == "opa_override"

    def test_require_both_false_both_deny(self):
        combined, rbac, opa = self._make_combined(require_both=False)
        rbac_denial = AuthorizationResult.deny(reason="rbac_denied")
        rbac.authorize.return_value = rbac_denial
        opa.authorize.return_value = AuthorizationResult.deny(reason="opa_denied")

        result = combined.authorize(_make_authz_request())
        assert not result.allowed
        assert result.reason == "rbac_denied"  # original RBAC denial returned

    def test_require_both_true_rbac_denies_skips_opa(self):
        combined, rbac, opa = self._make_combined(require_both=True)
        rbac.authorize.return_value = AuthorizationResult.deny(reason="rbac_no")

        result = combined.authorize(_make_authz_request())
        assert not result.allowed
        assert result.reason == "rbac_no"
        opa.authorize.assert_not_called()

    def test_require_both_true_both_allow(self):
        combined, rbac, opa = self._make_combined(require_both=True)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok", role="admin")
        opa.authorize.return_value = AuthorizationResult.allow(reason="opa_ok")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert "rbac_and_opa_allowed" in result.reason

    def test_require_both_true_rbac_allows_opa_denies(self):
        combined, rbac, opa = self._make_combined(require_both=True)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok", role="admin")
        opa.authorize.return_value = AuthorizationResult.deny(reason="opa_denied")

        result = combined.authorize(_make_authz_request())
        assert not result.allowed
        assert "rbac_allowed_but_opa_denied" in result.reason
