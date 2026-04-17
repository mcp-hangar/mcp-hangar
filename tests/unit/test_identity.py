"""Tests for identity value objects, extractors, and propagation."""

from dataclasses import FrozenInstanceError

import pytest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.infrastructure.identity.header_extractor import HeaderIdentityExtractor


class TestCallerIdentity:
    """Tests for CallerIdentity value object."""

    def test_anonymous_identity(self):
        identity = CallerIdentity(
            user_id=None,
            agent_id="agent-1",
            session_id="sess-1",
            principal_type="anonymous",
        )
        assert identity.principal_type == "anonymous"
        assert identity.user_id is None

    def test_user_identity(self):
        identity = CallerIdentity(
            user_id="user-42",
            agent_id="agent-1",
            session_id="sess-1",
            principal_type="user",
        )
        assert identity.user_id == "user-42"
        assert identity.principal_type == "user"

    def test_service_identity(self):
        identity = CallerIdentity(
            user_id="svc-account-1",
            agent_id=None,
            session_id=None,
            principal_type="service",
        )
        assert identity.principal_type == "service"

    def test_user_without_user_id_raises(self):
        with pytest.raises(ValueError, match="user_id cannot be None"):
            CallerIdentity(
                user_id=None,
                agent_id="agent-1",
                session_id=None,
                principal_type="user",
            )

    def test_service_without_user_id_raises(self):
        with pytest.raises(ValueError, match="user_id cannot be None"):
            CallerIdentity(
                user_id=None,
                agent_id=None,
                session_id=None,
                principal_type="service",
            )

    def test_frozen(self):
        identity = CallerIdentity(
            user_id="u1",
            agent_id=None,
            session_id=None,
            principal_type="user",
        )
        with pytest.raises((AttributeError, FrozenInstanceError)):
            setattr(identity, "user_id", "u2")


class TestIdentityContext:
    """Tests for IdentityContext value object."""

    def test_to_dict(self):
        ctx = IdentityContext(
            caller=CallerIdentity(
                user_id="u1",
                agent_id="a1",
                session_id="s1",
                principal_type="user",
            ),
            correlation_id="corr-42",
        )
        d = ctx.to_dict()
        assert d == {
            "user_id": "u1",
            "agent_id": "a1",
            "session_id": "s1",
            "principal_type": "user",
            "correlation_id": "corr-42",
        }

    def test_to_dict_minimal(self):
        ctx = IdentityContext(
            caller=CallerIdentity(
                user_id=None,
                agent_id="a1",
                session_id=None,
                principal_type="anonymous",
            ),
        )
        d = ctx.to_dict()
        assert d["user_id"] is None
        assert d["agent_id"] == "a1"
        assert d["correlation_id"] is None


class TestHeaderIdentityExtractor:
    """Tests for header-based identity extraction."""

    def test_extract_from_dict(self):
        extractor = HeaderIdentityExtractor()
        ctx = extractor.extract(
            {
                "x-user-id": "user-42",
                "x-agent-id": "agent-1",
                "x-session-id": "sess-1",
                "x-principal-type": "user",
                "x-correlation-id": "corr-123",
            }
        )
        assert ctx is not None
        assert ctx.caller.user_id == "user-42"
        assert ctx.caller.agent_id == "agent-1"
        assert ctx.caller.session_id == "sess-1"
        assert ctx.caller.principal_type == "user"
        assert ctx.correlation_id == "corr-123"

    def test_extract_from_tuple_list(self):
        """gRPC metadata comes as list of tuples."""
        extractor = HeaderIdentityExtractor()
        ctx = extractor.extract(
            [
                ("X-User-Id", "user-1"),
                ("X-Agent-Id", "agent-2"),
            ]
        )
        assert ctx is not None
        assert ctx.caller.user_id == "user-1"
        assert ctx.caller.agent_id == "agent-2"
        assert ctx.caller.principal_type == "anonymous"

    def test_no_identity_headers_returns_none(self):
        extractor = HeaderIdentityExtractor()
        ctx = extractor.extract({"content-type": "application/json"})
        assert ctx is None

    def test_none_metadata_returns_none(self):
        extractor = HeaderIdentityExtractor()
        assert extractor.extract(None) is None

    def test_case_insensitive_headers(self):
        extractor = HeaderIdentityExtractor()
        ctx = extractor.extract(
            {
                "X-USER-ID": "bob",
                "X-AGENT-ID": "a1",
            }
        )
        assert ctx is not None
        assert ctx.caller.user_id == "bob"

    def test_invalid_principal_type_falls_back(self):
        extractor = HeaderIdentityExtractor()
        ctx = extractor.extract(
            {
                "x-agent-id": "a1",
                "x-principal-type": "invalid_type",
            }
        )
        assert ctx is not None
        assert ctx.caller.principal_type == "anonymous"

    def test_custom_header_names(self):
        extractor = HeaderIdentityExtractor(
            user_id_header="X-Custom-User",
            agent_id_header="X-Custom-Agent",
        )
        ctx = extractor.extract(
            {
                "X-Custom-User": "u1",
                "X-Custom-Agent": "a1",
            }
        )
        assert ctx is not None
        assert ctx.caller.user_id == "u1"
        assert ctx.caller.agent_id == "a1"

    def test_only_agent_id_creates_anonymous(self):
        extractor = HeaderIdentityExtractor()
        ctx = extractor.extract({"x-agent-id": "a1"})
        assert ctx is not None
        assert ctx.caller.user_id is None
        assert ctx.caller.agent_id == "a1"
        assert ctx.caller.principal_type == "anonymous"

    def test_trusted_proxy_allows_header_identity(self):
        from mcp_hangar.infrastructure.identity.trusted_proxy import TrustedProxyResolver

        extractor = HeaderIdentityExtractor(trusted_proxies=TrustedProxyResolver(frozenset({"10.0.0.0/8"})))
        ctx = extractor.extract({"x-user-id": "alice"}, source_ip="10.1.2.3")

        assert ctx is not None
        assert ctx.caller.user_id == "alice"

    def test_untrusted_source_rejects_header_identity(self):
        from mcp_hangar.infrastructure.identity.trusted_proxy import TrustedProxyResolver

        extractor = HeaderIdentityExtractor(trusted_proxies=TrustedProxyResolver(frozenset({"10.0.0.0/8"})))

        assert extractor.extract({"x-user-id": "alice"}, source_ip="203.0.113.9") is None


class TestJWTIdentityExtractor:
    """Tests for JWT-based identity extraction."""

    def test_extract_valid_token(self):
        """JWT extraction with PyJWT."""
        try:
            import jwt as pyjwt
        except ImportError:
            pytest.skip("PyJWT not installed")

        from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor

        secret = "test-secret"
        token = pyjwt.encode(
            {"sub": "user-42", "agent_id": "a1", "sid": "s1", "type": "user", "jti": "corr-1"},
            secret,
            algorithm="HS256",
        )

        extractor = JWTIdentityExtractor(secret_or_key=secret)
        ctx = extractor.extract({"Authorization": f"Bearer {token}"})
        assert ctx is not None
        assert ctx.caller.user_id == "user-42"
        assert ctx.caller.agent_id == "a1"
        assert ctx.caller.session_id == "s1"
        assert ctx.caller.principal_type == "user"
        assert ctx.correlation_id == "corr-1"

    def test_extract_no_auth_header_returns_none(self):
        try:
            import jwt  # noqa: F401
        except ImportError:
            pytest.skip("PyJWT not installed")

        from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor

        extractor = JWTIdentityExtractor(secret_or_key="secret")
        assert extractor.extract({"content-type": "json"}) is None

    def test_extract_invalid_token_returns_none(self):
        try:
            import jwt  # noqa: F401
        except ImportError:
            pytest.skip("PyJWT not installed")

        from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor

        extractor = JWTIdentityExtractor(secret_or_key="correct-secret")
        ctx = extractor.extract({"Authorization": "Bearer invalid.token.here"})
        assert ctx is None

    def test_extract_expired_token_returns_none(self):
        try:
            import jwt as pyjwt
        except ImportError:
            pytest.skip("PyJWT not installed")

        import time

        from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor

        secret = "test-secret"
        token = pyjwt.encode(
            {"sub": "user-42", "exp": int(time.time()) - 3600},
            secret,
            algorithm="HS256",
        )

        extractor = JWTIdentityExtractor(secret_or_key=secret)
        ctx = extractor.extract({"Authorization": f"Bearer {token}"})
        assert ctx is None

    def test_extract_none_metadata(self):
        try:
            import jwt  # noqa: F401
        except ImportError:
            pytest.skip("PyJWT not installed")

        from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor

        extractor = JWTIdentityExtractor(secret_or_key="secret")
        assert extractor.extract(None) is None


class TestIdentityContextvar:
    """Test that identity_context propagates via contextvars."""

    def test_bind_and_read(self):
        from mcp_hangar.context import (
            bind_request_context,
            clear_request_context,
            get_identity_context,
        )

        ctx = IdentityContext(
            caller=CallerIdentity(
                user_id="u1",
                agent_id="a1",
                session_id=None,
                principal_type="user",
            ),
        )

        bind_request_context(identity_context=ctx)
        assert get_identity_context() is ctx
        clear_request_context()
        assert get_identity_context() is None

    def test_no_context_returns_none(self):
        from mcp_hangar.context import clear_request_context, get_identity_context

        clear_request_context()
        assert get_identity_context() is None
