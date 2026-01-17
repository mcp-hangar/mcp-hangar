#!/usr/bin/env python3
"""
Comprehensive security test suite for MCP-Hangar Auth.

This test suite covers:
1. Brute-force protection
2. Token expiration handling
3. Key revocation propagation
4. Concurrent access safety
5. Input validation edge cases
6. Authorization bypass attempts
7. Token replay attacks
8. Timing attack resistance
"""

from concurrent.futures import as_completed, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest
from mcp_hangar.domain.exceptions import ExpiredCredentialsError, InvalidCredentialsError, RevokedCredentialsError
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType
from mcp_hangar.infrastructure.auth.api_key_authenticator import (
    ApiKeyAuthenticator,
    InMemoryApiKeyStore,
    MAX_API_KEY_LENGTH,
)
from mcp_hangar.infrastructure.auth.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer

# Import auth components
from mcp_hangar.server.auth_bootstrap import bootstrap_auth
from mcp_hangar.server.auth_config import AuthConfig


class TestBruteForceProtection:
    """Test brute-force attack protection."""

    def test_rapid_failed_attempts_should_be_rate_limited(self):
        """Rapid failed login attempts should trigger rate limiting."""
        # Note: Currently NO rate limiting on auth - this is a GAP
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)

        failed_count = 0
        start = time.time()

        # Try 100 invalid keys rapidly
        for i in range(100):
            request = AuthRequest(
                headers={"X-API-Key": f"mcp_invalid_key_{i}"},
                source_ip="192.168.1.100",
                method="POST",
                path="/mcp",
            )
            try:
                auth.authenticate(request)
            except InvalidCredentialsError:
                failed_count += 1

        elapsed = time.time() - start

        # Current behavior: All 100 fail instantly (no rate limiting)
        assert failed_count == 100
        # This SHOULD take longer with rate limiting
        print(f"100 failed attempts took {elapsed:.3f}s (should be rate-limited)")

        # GAP: No rate limiting is applied
        # RECOMMENDATION: Add rate limiting per IP

    def test_key_enumeration_via_timing(self):
        """Test that invalid keys take same time as valid keys (timing attack)."""
        store = InMemoryApiKeyStore()

        # Create a valid key
        valid_key = store.create_key("test-user", "test-key")

        auth = ApiKeyAuthenticator(store)

        # Measure time for valid key
        valid_times = []
        for _ in range(10):
            request = AuthRequest(
                headers={"X-API-Key": valid_key},
                source_ip="127.0.0.1",
                method="POST",
                path="/mcp",
            )
            start = time.perf_counter()
            try:
                auth.authenticate(request)
            except Exception:
                pass
            valid_times.append(time.perf_counter() - start)

        # Measure time for invalid key
        invalid_times = []
        for _ in range(10):
            request = AuthRequest(
                headers={"X-API-Key": "mcp_totally_invalid_key_12345"},
                source_ip="127.0.0.1",
                method="POST",
                path="/mcp",
            )
            start = time.perf_counter()
            try:
                auth.authenticate(request)
            except Exception:
                pass
            invalid_times.append(time.perf_counter() - start)

        avg_valid = sum(valid_times) / len(valid_times)
        avg_invalid = sum(invalid_times) / len(invalid_times)

        print(f"Valid key avg: {avg_valid*1000:.3f}ms")
        print(f"Invalid key avg: {avg_invalid*1000:.3f}ms")
        print(f"Difference: {abs(avg_valid - avg_invalid)*1000:.3f}ms")

        # GAP: Dictionary lookup is NOT constant-time
        # RECOMMENDATION: Use constant-time comparison


class TestTokenExpiration:
    """Test token expiration handling."""

    def test_expired_key_is_rejected(self):
        """Expired API key should be rejected."""
        store = InMemoryApiKeyStore()

        # Create key that expired 1 hour ago
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        key = store.create_key("test-user", "expired-key", expires_at=expired_time)

        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": key},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )

        with pytest.raises(ExpiredCredentialsError):
            auth.authenticate(request)

    def test_key_expiring_during_request(self):
        """Key expiring during a long request should be handled."""
        store = InMemoryApiKeyStore()

        # Create key expiring in 100ms
        expires_soon = datetime.now(timezone.utc) + timedelta(milliseconds=100)
        key = store.create_key("test-user", "expiring-key", expires_at=expires_soon)

        auth = ApiKeyAuthenticator(store)

        # First request should succeed
        request = AuthRequest(
            headers={"X-API-Key": key},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )
        principal = auth.authenticate(request)
        assert principal is not None

        # Wait for expiration
        time.sleep(0.2)

        # Second request should fail
        with pytest.raises(ExpiredCredentialsError):
            auth.authenticate(request)


class TestKeyRevocation:
    """Test key revocation."""

    def test_revoked_key_is_immediately_rejected(self):
        """Revoked key should be rejected immediately."""
        store = InMemoryApiKeyStore()
        key = store.create_key("test-user", "to-revoke")

        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": key},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )

        # Should work before revocation
        principal = auth.authenticate(request)
        assert principal is not None

        # Revoke the key
        # Need to find key_id first
        keys = store.list_keys("test-user")
        key_id = keys[0].key_id
        store.revoke_key(key_id)

        # Should fail after revocation
        with pytest.raises(RevokedCredentialsError):
            auth.authenticate(request)

    def test_revocation_during_active_session(self):
        """
        Key revoked during active operations should affect next auth check.

        GAP: Currently no session concept - each request re-authenticates.
        This is actually GOOD for security but may impact performance.
        """
        pass  # This is by design - stateless auth


class TestConcurrentAccess:
    """Test concurrent access safety."""

    def test_concurrent_key_creation(self):
        """Multiple threads creating keys simultaneously."""
        store = InMemoryApiKeyStore()
        errors = []
        keys_created = []

        def create_key(i):
            try:
                key = store.create_key(f"user-{i}", f"key-{i}")
                keys_created.append(key)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(create_key, i) for i in range(50)]
            for f in as_completed(futures):
                pass

        assert len(errors) == 0, f"Errors during concurrent creation: {errors}"
        assert len(keys_created) == 50

    def test_concurrent_authentication(self):
        """Multiple threads authenticating simultaneously."""
        store = InMemoryApiKeyStore()
        key = store.create_key("shared-user", "shared-key")
        auth = ApiKeyAuthenticator(store)

        successes = []
        errors = []

        def authenticate():
            try:
                request = AuthRequest(
                    headers={"X-API-Key": key},
                    source_ip="127.0.0.1",
                    method="POST",
                    path="/mcp",
                )
                principal = auth.authenticate(request)
                successes.append(principal)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(authenticate) for _ in range(100)]
            for f in as_completed(futures):
                pass

        assert len(errors) == 0
        assert len(successes) == 100

    def test_concurrent_role_assignment(self):
        """Multiple threads assigning roles simultaneously."""
        store = InMemoryRoleStore()
        errors = []

        def assign_role(i):
            try:
                store.assign_role(f"user-{i % 10}", "developer")
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(assign_role, i) for i in range(100)]
            for f in as_completed(futures):
                pass

        assert len(errors) == 0


class TestInputValidation:
    """Test input validation edge cases."""

    def test_empty_api_key(self):
        """Empty API key should be rejected."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)

        request = AuthRequest(
            headers={"X-API-Key": ""},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )

        with pytest.raises(InvalidCredentialsError):
            auth.authenticate(request)

    def test_very_long_api_key(self):
        """Very long API key should be rejected (DoS prevention)."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)

        # Create a key longer than MAX_API_KEY_LENGTH
        long_key = "mcp_" + "x" * (MAX_API_KEY_LENGTH + 100)

        request = AuthRequest(
            headers={"X-API-Key": long_key},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )

        with pytest.raises(InvalidCredentialsError):
            auth.authenticate(request)

    def test_unicode_in_api_key(self):
        """Unicode characters in API key should be handled safely."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)

        unicode_key = "mcp_тест🔑键"

        request = AuthRequest(
            headers={"X-API-Key": unicode_key},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )

        # Should fail gracefully (not crash)
        with pytest.raises(InvalidCredentialsError):
            auth.authenticate(request)

    def test_null_bytes_in_key(self):
        """Null bytes in API key should be rejected."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)

        null_key = "mcp_test\x00key"

        request = AuthRequest(
            headers={"X-API-Key": null_key},
            source_ip="127.0.0.1",
            method="POST",
            path="/mcp",
        )

        with pytest.raises(InvalidCredentialsError):
            auth.authenticate(request)

    def test_sql_injection_in_principal_id(self):
        """SQL injection attempt in principal ID should be safe."""
        store = InMemoryApiKeyStore()

        # Attempt SQL injection
        malicious_id = "'; DROP TABLE users; --"

        # Should not crash or execute SQL
        try:
            store.create_key(malicious_id, "test-key")
            # With in-memory store, this is safe
            # With SQL backend, this MUST be parameterized
        except ValueError:
            pass  # Rejected by PrincipalId validation


class TestAuthorizationBypass:
    """Test authorization bypass attempts."""

    def test_anonymous_principal_has_no_roles(self):
        """Anonymous principal should have no special privileges."""
        principal = Principal.anonymous()

        store = InMemoryRoleStore()
        auth = RBACAuthorizer(store)

        request = AuthorizationRequest(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="any-tool",
        )

        result = auth.authorize(request)
        assert not result.allowed

    def test_cannot_escalate_own_permissions(self):
        """User cannot grant themselves higher permissions."""
        store = InMemoryRoleStore()

        # Create a developer user
        developer = Principal(
            id=PrincipalId("developer-user"),
            type=PrincipalType.USER,
            groups=frozenset(),
        )
        store.assign_role("developer-user", "developer")

        auth = RBACAuthorizer(store)

        # Developer trying to assign admin role
        # Note: This would be done via admin API, not directly
        # The auth layer should protect such endpoints
        request = AuthorizationRequest(
            principal=developer,
            action="assign",
            resource_type="role",
            resource_id="admin",
        )

        result = auth.authorize(request)
        # Developer should NOT be able to assign roles
        assert not result.allowed

    def test_group_spoofing_attempt(self):
        """Cannot claim membership in groups not assigned."""
        store = InMemoryRoleStore()
        store.assign_role("group:admins", "admin")

        # Create user claiming to be in admins group
        # but the groups come from the token, not user input
        fake_admin = Principal(
            id=PrincipalId("attacker"),
            type=PrincipalType.USER,
            groups=frozenset(["admins"]),  # This would come from JWT
        )

        auth = RBACAuthorizer(store)

        request = AuthorizationRequest(
            principal=fake_admin,
            action="delete",
            resource_type="provider",
            resource_id="critical-provider",
        )

        result = auth.authorize(request)
        # If groups come from trusted JWT, this WOULD be allowed
        # The security is in the JWT validation, not here
        assert result.allowed  # This is expected - groups are trusted


class TestTokenReplay:
    """Test token replay attack resistance."""

    def test_same_token_can_be_used_multiple_times(self):
        """
        API keys can be reused (this is by design).
        JWT tokens should have short expiry for replay protection.
        """
        store = InMemoryApiKeyStore()
        key = store.create_key("test-user", "reusable-key")
        auth = ApiKeyAuthenticator(store)

        # Use same key multiple times
        for _ in range(5):
            request = AuthRequest(
                headers={"X-API-Key": key},
                source_ip="127.0.0.1",
                method="POST",
                path="/mcp",
            )
            principal = auth.authenticate(request)
            assert principal is not None

    def test_key_from_different_ip_is_allowed(self):
        """
        API key from different IP is allowed.

        GAP: No IP binding for API keys.
        RECOMMENDATION: Add optional IP allowlist per key.
        """
        store = InMemoryApiKeyStore()
        key = store.create_key("test-user", "mobile-key")
        auth = ApiKeyAuthenticator(store)

        # Use from different IPs
        for ip in ["192.168.1.1", "10.0.0.1", "172.16.0.1"]:
            request = AuthRequest(
                headers={"X-API-Key": key},
                source_ip=ip,
                method="POST",
                path="/mcp",
            )
            principal = auth.authenticate(request)
            assert principal is not None


class TestAuditLogging:
    """Test audit logging completeness."""

    def test_failed_auth_is_logged(self):
        """Failed authentication attempts should be logged."""
        # Check that we emit AuthenticationFailed events
        from mcp_hangar.domain.events import AuthenticationFailed

        events = []

        def capture_event(event):
            events.append(event)

        config = AuthConfig(enabled=True)
        auth_components = bootstrap_auth(config, event_publisher=capture_event)

        # Create invalid request
        request = AuthRequest(
            headers={"X-API-Key": "mcp_invalid_key"},
            source_ip="192.168.1.1",
            method="POST",
            path="/mcp",
        )

        try:
            auth_components.authn_middleware.authenticate(request)
        except Exception:
            pass

        # Check that failure was logged
        failed_events = [e for e in events if isinstance(e, AuthenticationFailed)]
        assert len(failed_events) == 1
        assert failed_events[0].source_ip == "192.168.1.1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
