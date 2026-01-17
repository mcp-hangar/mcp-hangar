"""Unit tests for authentication components.

Tests cover:
- API Key authenticator and store
- JWT authenticator
- Value objects (Principal, Permission, Role)
"""

from datetime import datetime, timedelta, timezone

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.exceptions import ExpiredCredentialsError, InvalidCredentialsError, RevokedCredentialsError
from mcp_hangar.domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role
from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore


class TestPrincipalId:
    """Tests for PrincipalId value object."""

    def test_valid_principal_id(self):
        """Valid principal ID is created successfully."""
        pid = PrincipalId("user:john@example.com")
        assert pid.value == "user:john@example.com"
        assert str(pid) == "user:john@example.com"

    def test_empty_principal_id_raises(self):
        """Empty principal ID raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            PrincipalId("")

    def test_too_long_principal_id_raises(self):
        """Too long principal ID raises ValueError."""
        with pytest.raises(ValueError, match="1-256 characters"):
            PrincipalId("a" * 257)

    def test_invalid_characters_raises(self):
        """Invalid characters in principal ID raises ValueError."""
        with pytest.raises(ValueError, match="invalid characters"):
            PrincipalId("user:john<script>")


class TestPrincipal:
    """Tests for Principal value object."""

    def test_system_principal(self):
        """System principal is created correctly."""
        principal = Principal.system()
        assert principal.id.value == "system"
        assert principal.type == PrincipalType.SYSTEM
        assert principal.is_system()

    def test_anonymous_principal(self):
        """Anonymous principal is created correctly."""
        principal = Principal.anonymous()
        assert principal.id.value == "anonymous"
        assert principal.type == PrincipalType.USER
        assert principal.is_anonymous()

    def test_principal_with_groups(self):
        """Principal with groups is created correctly."""
        principal = Principal(
            id=PrincipalId("user:john"),
            type=PrincipalType.USER,
            groups=frozenset(["developers", "team-alpha"]),
        )
        assert principal.in_group("developers")
        assert principal.in_group("team-alpha")
        assert not principal.in_group("admins")

    def test_principal_with_tenant(self):
        """Principal with tenant is created correctly."""
        principal = Principal(
            id=PrincipalId("user:john"),
            type=PrincipalType.USER,
            tenant_id="acme-corp",
        )
        assert principal.tenant_id == "acme-corp"


class TestPermission:
    """Tests for Permission value object."""

    def test_permission_creation(self):
        """Permission is created correctly."""
        perm = Permission("provider", "read", "*")
        assert perm.resource_type == "provider"
        assert perm.action == "read"
        assert perm.resource_id == "*"

    def test_permission_string(self):
        """Permission string representation is correct."""
        perm = Permission("provider", "read", "my-provider")
        assert str(perm) == "provider:read:my-provider"

    def test_wildcard_matches_all(self):
        """Wildcard permission matches any resource."""
        perm = Permission("*", "*", "*")
        assert perm.matches("provider", "read", "my-provider")
        assert perm.matches("tool", "invoke", "math:add")

    def test_specific_permission_matches_exact(self):
        """Specific permission matches only exact values."""
        perm = Permission("provider", "read", "my-provider")
        assert perm.matches("provider", "read", "my-provider")
        assert not perm.matches("provider", "read", "other-provider")
        assert not perm.matches("provider", "write", "my-provider")

    def test_parse_permission_two_parts(self):
        """Permission parses from two-part string."""
        perm = Permission.parse("provider:read")
        assert perm.resource_type == "provider"
        assert perm.action == "read"
        assert perm.resource_id == "*"

    def test_parse_permission_three_parts(self):
        """Permission parses from three-part string."""
        perm = Permission.parse("tool:invoke:math:add")
        assert perm.resource_type == "tool"
        assert perm.action == "invoke"
        assert perm.resource_id == "math:add"

    def test_parse_invalid_permission_raises(self):
        """Invalid permission format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid permission format"):
            Permission.parse("invalid")


class TestRole:
    """Tests for Role value object."""

    def test_role_creation(self):
        """Role is created correctly."""
        perms = frozenset(
            [
                Permission("provider", "read"),
                Permission("tool", "invoke"),
            ]
        )
        role = Role("developer", perms, "Developer role")
        assert role.name == "developer"
        assert len(role.permissions) == 2
        assert role.description == "Developer role"

    def test_role_has_permission(self):
        """Role correctly checks permission."""
        perms = frozenset([Permission("provider", "read")])
        role = Role("viewer", perms)
        assert role.has_permission("provider", "read", "*")
        assert not role.has_permission("provider", "delete", "*")

    def test_invalid_role_name_raises(self):
        """Invalid role name raises ValueError."""
        with pytest.raises(ValueError, match="invalid characters"):
            Role("admin<script>", frozenset())


class TestApiKeyAuthenticator:
    """Tests for API Key authenticator."""

    def test_authenticate_valid_key(self):
        """Valid API key returns correct principal."""
        store = InMemoryApiKeyStore()
        raw_key = store.create_key(
            principal_id="service:ci-pipeline",
            name="CI Key",
        )

        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": raw_key},
            source_ip="127.0.0.1",
        )

        assert auth.supports(request)
        principal = auth.authenticate(request)
        assert principal.id.value == "service:ci-pipeline"
        assert principal.type == PrincipalType.SERVICE_ACCOUNT

    def test_authenticate_invalid_key_raises(self):
        """Invalid key raises InvalidCredentialsError."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": "mcp_invalid_key_12345678901234"},
            source_ip="127.0.0.1",
        )

        with pytest.raises(InvalidCredentialsError, match="Invalid API key"):
            auth.authenticate(request)

    def test_authenticate_invalid_format_raises(self):
        """Key without prefix raises InvalidCredentialsError."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": "not_a_valid_key"},
            source_ip="127.0.0.1",
        )

        with pytest.raises(InvalidCredentialsError, match="must start with"):
            auth.authenticate(request)

    def test_authenticate_expired_key_raises(self):
        """Expired key raises ExpiredCredentialsError."""
        store = InMemoryApiKeyStore()
        raw_key = store.create_key(
            principal_id="service:expired",
            name="Expired Key",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": raw_key},
            source_ip="127.0.0.1",
        )

        with pytest.raises(ExpiredCredentialsError, match="expired"):
            auth.authenticate(request)

    def test_authenticate_revoked_key_raises(self):
        """Revoked key raises RevokedCredentialsError."""
        store = InMemoryApiKeyStore()
        raw_key = store.create_key(
            principal_id="service:revoked",
            name="Revoked Key",
        )

        # Get key metadata to find key_id
        keys = store.list_keys("service:revoked")
        store.revoke_key(keys[0].key_id)

        auth = ApiKeyAuthenticator(store)
        request = AuthRequest(
            headers={"X-API-Key": raw_key},
            source_ip="127.0.0.1",
        )

        with pytest.raises(RevokedCredentialsError, match="revoked"):
            auth.authenticate(request)

    def test_supports_detects_header(self):
        """supports() correctly detects API key header."""
        store = InMemoryApiKeyStore()
        auth = ApiKeyAuthenticator(store)

        with_header = AuthRequest(
            headers={"X-API-Key": "mcp_something"},
            source_ip="127.0.0.1",
        )
        without_header = AuthRequest(
            headers={"Authorization": "Bearer token"},
            source_ip="127.0.0.1",
        )

        assert auth.supports(with_header)
        assert not auth.supports(without_header)

    def test_custom_header_name(self):
        """Custom header name is respected."""
        store = InMemoryApiKeyStore()
        raw_key = store.create_key(
            principal_id="service:custom",
            name="Custom Header Key",
        )

        auth = ApiKeyAuthenticator(store, header_name="X-Custom-Key")
        request = AuthRequest(
            headers={"X-Custom-Key": raw_key},
            source_ip="127.0.0.1",
        )

        assert auth.supports(request)
        principal = auth.authenticate(request)
        assert principal.id.value == "service:custom"


class TestInMemoryApiKeyStore:
    """Tests for in-memory API key store."""

    def test_create_and_get_key(self):
        """Create key and retrieve principal."""
        store = InMemoryApiKeyStore()
        raw_key = store.create_key(
            principal_id="user:admin",
            name="Admin Key",
            groups=frozenset(["admins"]),
            tenant_id="acme",
        )

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = store.get_principal_for_key(key_hash)

        assert principal is not None
        assert principal.id.value == "user:admin"
        assert principal.in_group("admins")
        assert principal.tenant_id == "acme"

    def test_list_keys(self):
        """List keys for principal."""
        store = InMemoryApiKeyStore()
        store.create_key("user:dev", "Key 1")
        store.create_key("user:dev", "Key 2")
        store.create_key("user:other", "Other Key")

        keys = store.list_keys("user:dev")
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"Key 1", "Key 2"}

    def test_revoke_key(self):
        """Revoke key works correctly."""
        store = InMemoryApiKeyStore()
        store.create_key("user:test", "Test Key")

        keys = store.list_keys("user:test")
        assert len(keys) == 1
        assert not keys[0].revoked

        store.revoke_key(keys[0].key_id)

        keys = store.list_keys("user:test")
        assert keys[0].revoked

    def test_count_keys(self):
        """Count keys correctly."""
        store = InMemoryApiKeyStore()
        assert store.count_keys("user:a") == 0
        assert store.count_all_keys() == 0
        assert store.count_all_active_keys() == 0

        store.create_key("user:a", "Key A")
        store.create_key("user:b", "Key B")

        assert store.count_keys("user:a") == 1
        assert store.count_keys("user:b") == 1
        assert store.count_all_keys() == 2
        assert store.count_all_active_keys() == 2

        # Revoke one
        keys = store.list_keys("user:a")
        store.revoke_key(keys[0].key_id)

        assert store.count_keys("user:a") == 0  # No active keys
        assert store.count_all_keys() == 2
        assert store.count_all_active_keys() == 1

    def test_key_last_used_updated(self):
        """Last used timestamp is updated on access."""
        store = InMemoryApiKeyStore()
        raw_key = store.create_key("user:test", "Test Key")

        keys = store.list_keys("user:test")
        assert keys[0].last_used_at is None

        # Access the key
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        store.get_principal_for_key(key_hash)

        keys = store.list_keys("user:test")
        assert keys[0].last_used_at is not None
