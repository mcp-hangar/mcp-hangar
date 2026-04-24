"""Security regression tests for identity and network hardening."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportPrivateUsage=false, reportUnusedParameter=false

import pytest
from starlette.testclient import TestClient

from mcp_hangar.domain.security.ssrf import validate_no_ssrf
from mcp_hangar.infrastructure.identity.header_extractor import HeaderIdentityExtractor
from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor
from mcp_hangar.infrastructure.identity.trusted_proxy import TrustedProxyResolver
from mcp_hangar.server.api.router import create_api_router
from mcp_hangar.server.api.sessions import _SuspendedSessionCache


pytestmark = pytest.mark.security


def test_w1_header_identity_ignores_untrusted_source() -> None:
    extractor = HeaderIdentityExtractor(
        trusted_proxies=TrustedProxyResolver(frozenset({"10.0.0.1"})),
    )
    headers = {"x-user-id": "attacker", "x-principal-type": "user"}

    assert extractor.extract(headers, source_ip="192.168.1.100") is None

    identity = extractor.extract(headers, source_ip="10.0.0.1")
    assert identity is not None
    assert identity.caller.user_id == "attacker"
    assert identity.caller.principal_type == "user"


def test_w2_ssrf_blocks_metadata_endpoint() -> None:
    with pytest.raises(ValueError, match="SSRF blocked"):
        validate_no_ssrf("http://169.254.169.254/latest/meta-data/")


def test_w3_ssrf_blocks_localhost() -> None:
    with pytest.raises(ValueError, match="SSRF blocked"):
        validate_no_ssrf("http://127.0.0.1:5432")


def test_w4_suspended_sessions_bounded() -> None:
    cache = _SuspendedSessionCache(maxsize=10, ttl=86400)

    for index in range(10):
        cache.add(f"session-{index}")

    for index in range(10):
        assert f"session-{index}" in cache

    cache.add("session-10")

    assert "session-0" not in cache
    assert "session-10" in cache


def test_w4_suspend_session_rejects_invalid_session_id() -> None:
    client = TestClient(create_api_router(), base_url="http://localhost")

    response = client.post("/sessions/invalid!session/suspend", json={"reason": "test"})

    assert response.status_code == 400
    assert response.json() == {"error": "invalid session_id: must be 1-128 alphanumeric, dash, or underscore"}


def test_w5_jwt_mixed_algorithm_families_rejected() -> None:
    with pytest.raises(ValueError, match="Mixed JWT algorithm families"):
        JWTIdentityExtractor._validate_algorithms(["HS256", "RS256"], "secret")


def test_w5_jwt_single_algorithm_family_allowed() -> None:
    JWTIdentityExtractor._validate_algorithms(["HS256", "HS512"], "secret")
    public_key = "-----BEGIN PUBLIC KEY-----\nkey\n-----END PUBLIC KEY-----"
    JWTIdentityExtractor._validate_algorithms(["RS256", "ES256"], public_key)


def test_csrf_header_required_on_mutating_requests() -> None:
    client = TestClient(create_api_router(), base_url="http://localhost")

    response = client.post(
        "/sessions/test-session/suspend",
        json={"reason": "test"},
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": "csrf_header_required",
        "message": "X-Requested-With header is required for mutating requests",
    }


def test_csrf_header_not_required_with_api_key() -> None:
    client = TestClient(create_api_router(), base_url="http://localhost")

    response = client.post(
        "/sessions/test-session/suspend",
        json={"reason": "test"},
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code != 403
    assert response.json() == {"session_id": "test-session", "suspended": True}


def test_csrf_header_not_required_on_get() -> None:
    client = TestClient(create_api_router(), base_url="http://localhost")

    response = client.get("/system/me")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "principal": None}
