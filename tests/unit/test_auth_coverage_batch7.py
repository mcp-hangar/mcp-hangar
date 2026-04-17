# pyright: reportArgumentType=false, reportMissingTypeArgument=false

"""Auth coverage batch 7 -- LicenseValidator, SecretsResolver, auth config,
roles stub fallbacks, JWTIdentityExtractor uncovered branches, and
OutputRedactor remaining edge cases.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.domain.value_objects.license import LicenseTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HMAC_SECRET = b"hangar-v1-license-signing-key"


def _make_license_key(
    tier: str,
    org: str,
    expires_at: float,
) -> str:
    """Build a valid ``hk_v1_<base64>`` license key for testing."""
    payload = {"tier": tier, "org": org, "expires_at": expires_at}
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    sig = hmac.new(_HMAC_SECRET, payload_bytes, hashlib.sha256).hexdigest()
    payload["signature"] = sig
    return "hk_v1_" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


# ===================================================================
# 1. LicenseValidator
# ===================================================================


class TestLicenseValidatorNoKey:
    """Validate behaviour when no license key is supplied."""

    def test_none_returns_community(self) -> None:
        from enterprise.auth.license import LicenseValidator

        result = LicenseValidator().validate(None)
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "no_license_key"

    def test_empty_string_returns_community(self) -> None:
        from enterprise.auth.license import LicenseValidator

        result = LicenseValidator().validate("")
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "no_license_key"


class TestLicenseValidatorBadPrefix:
    def test_wrong_prefix_returns_community(self) -> None:
        from enterprise.auth.license import LicenseValidator

        result = LicenseValidator().validate("bad_prefix_key")
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "bad_prefix"


class TestLicenseValidatorDecodeFailures:
    def test_invalid_base64_returns_decode_failed(self) -> None:
        from enterprise.auth.license import LicenseValidator

        result = LicenseValidator().validate("hk_v1_!!!not-base64!!!")
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "decode_failed"

    def test_valid_base64_but_invalid_json_returns_decode_failed(self) -> None:
        from enterprise.auth.license import LicenseValidator

        encoded = base64.urlsafe_b64encode(b"not-json").decode()
        result = LicenseValidator().validate(f"hk_v1_{encoded}")
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "decode_failed"


class TestLicenseValidatorBadSignature:
    def test_tampered_signature_returns_bad_signature(self) -> None:
        from enterprise.auth.license import LicenseValidator

        payload = {
            "tier": "pro",
            "org": "test-org",
            "expires_at": time.time() + 86400,
            "signature": "00" * 32,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        result = LicenseValidator().validate(f"hk_v1_{encoded}")
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "bad_signature"


class TestLicenseValidatorExpiry:
    def test_expired_beyond_grace_returns_community(self) -> None:
        from enterprise.auth.license import LicenseValidator

        now = 1_700_000_000.0
        expires_at = now - (8 * 86400)  # 8 days ago (beyond 7-day grace)
        key = _make_license_key("pro", "test-org", expires_at)
        with patch("enterprise.auth.license.time") as mock_time:
            mock_time.time.return_value = now
            result = LicenseValidator().validate(key)
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "expired_beyond_grace"

    def test_expired_within_grace_period_retains_tier(self) -> None:
        from enterprise.auth.license import LicenseValidator

        now = 1_700_000_000.0
        expires_at = now - (3 * 86400)  # 3 days ago (within 7-day grace)
        key = _make_license_key("pro", "test-org", expires_at)
        with patch("enterprise.auth.license.time") as mock_time:
            mock_time.time.return_value = now
            result = LicenseValidator().validate(key)
        assert result.tier is LicenseTier.PRO
        assert result.grace_period is True
        assert result.error is None


class TestLicenseValidatorUnknownTier:
    def test_unknown_tier_returns_community(self) -> None:
        from enterprise.auth.license import LicenseValidator

        now = 1_700_000_000.0
        key = _make_license_key("ultra", "test-org", now + 86400)
        with patch("enterprise.auth.license.time") as mock_time:
            mock_time.time.return_value = now
            result = LicenseValidator().validate(key)
        assert result.tier is LicenseTier.COMMUNITY
        assert result.error == "unknown_tier"


class TestLicenseValidatorValidKeys:
    def test_valid_pro_key(self) -> None:
        from enterprise.auth.license import LicenseValidator

        now = 1_700_000_000.0
        key = _make_license_key("pro", "acme-inc", now + 86400)
        with patch("enterprise.auth.license.time") as mock_time:
            mock_time.time.return_value = now
            result = LicenseValidator().validate(key)
        assert result.tier is LicenseTier.PRO
        assert result.org == "acme-inc"
        assert result.grace_period is False
        assert result.error is None

    def test_valid_enterprise_key(self) -> None:
        from enterprise.auth.license import LicenseValidator

        now = 1_700_000_000.0
        key = _make_license_key("enterprise", "mega-corp", now + 86400)
        with patch("enterprise.auth.license.time") as mock_time:
            mock_time.time.return_value = now
            result = LicenseValidator().validate(key)
        assert result.tier is LicenseTier.ENTERPRISE
        assert result.org == "mega-corp"

    def test_valid_community_key(self) -> None:
        from enterprise.auth.license import LicenseValidator

        now = 1_700_000_000.0
        key = _make_license_key("community", "open-org", now + 86400)
        with patch("enterprise.auth.license.time") as mock_time:
            mock_time.time.return_value = now
            result = LicenseValidator().validate(key)
        assert result.tier is LicenseTier.COMMUNITY
        assert result.org == "open-org"
        assert result.error is None


# ===================================================================
# 2. SecretsResolver
# ===================================================================


class TestSecretsResult:
    def test_all_resolved_when_no_missing(self) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResult

        result = SecretsResult(resolved={"A": "1"}, missing=[], sources={"A": "env"})
        assert result.all_resolved is True

    def test_not_all_resolved_when_missing_present(self) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResult

        result = SecretsResult(resolved={}, missing=["A"], sources={})
        assert result.all_resolved is False


class TestSecretsResolverEnvVars:
    def test_resolves_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.setenv("MY_SECRET", "secret_val")
        resolver = SecretsResolver(secrets_dir=Path("/nonexistent"))
        result = resolver.resolve(["MY_SECRET"], "test-provider")
        assert result.resolved["MY_SECRET"] == "secret_val"
        assert result.sources["MY_SECRET"] == "env"
        assert result.all_resolved is True


class TestSecretsResolverFiles:
    def test_resolves_from_provider_specific_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.delenv("PROVIDER_SECRET", raising=False)
        provider_dir = tmp_path / "my-provider"
        provider_dir.mkdir()
        (provider_dir / "PROVIDER_SECRET").write_text("  file_val  \n")
        resolver = SecretsResolver(secrets_dir=tmp_path)
        result = resolver.resolve(["PROVIDER_SECRET"], "my-provider")
        assert result.resolved["PROVIDER_SECRET"] == "file_val"
        assert result.sources["PROVIDER_SECRET"] == "file"

    def test_resolves_from_global_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.delenv("GLOBAL_SECRET", raising=False)
        (tmp_path / "GLOBAL_SECRET").write_text("global_val")
        resolver = SecretsResolver(secrets_dir=tmp_path)
        result = resolver.resolve(["GLOBAL_SECRET"], "my-provider")
        assert result.resolved["GLOBAL_SECRET"] == "global_val"
        assert result.sources["GLOBAL_SECRET"] == "file"

    def test_missing_secret(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.delenv("NOPE", raising=False)
        resolver = SecretsResolver(secrets_dir=tmp_path)
        result = resolver.resolve(["NOPE"], "my-provider")
        assert "NOPE" in result.missing
        assert result.all_resolved is False

    def test_provider_file_takes_precedence_over_global(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.delenv("SECRET", raising=False)
        (tmp_path / "SECRET").write_text("global")
        provider_dir = tmp_path / "prov"
        provider_dir.mkdir()
        (provider_dir / "SECRET").write_text("provider")
        resolver = SecretsResolver(secrets_dir=tmp_path)
        result = resolver.resolve(["SECRET"], "prov")
        assert result.resolved["SECRET"] == "provider"

    def test_env_takes_precedence_over_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.setenv("SECRET", "from_env")
        (tmp_path / "SECRET").write_text("from_file")
        resolver = SecretsResolver(secrets_dir=tmp_path)
        result = resolver.resolve(["SECRET"], "prov")
        assert result.resolved["SECRET"] == "from_env"
        assert result.sources["SECRET"] == "env"


class TestSecretsResolverFileErrors:
    def test_unreadable_provider_file_falls_through_to_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.delenv("SECRET", raising=False)
        provider_dir = tmp_path / "prov"
        provider_dir.mkdir()
        provider_file = provider_dir / "SECRET"
        provider_file.write_text("provider_val")
        (tmp_path / "SECRET").write_text("global_val")
        resolver = SecretsResolver(secrets_dir=tmp_path)

        original_read_text = Path.read_text

        def read_text_with_provider_error(self: Path, *args: object, **kwargs: object) -> str:
            if self == provider_file:
                raise PermissionError("provider file unreadable")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", autospec=True, side_effect=read_text_with_provider_error):
            result = resolver.resolve(["SECRET"], "prov")

        # Should fall through to global file
        assert result.resolved["SECRET"] == "global_val"

    def test_unreadable_global_file_results_in_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        monkeypatch.delenv("SECRET", raising=False)
        global_file = tmp_path / "SECRET"
        global_file.write_text("global_val")
        resolver = SecretsResolver(secrets_dir=tmp_path)

        original_read_text = Path.read_text

        def read_text_with_global_error(self: Path, *args: object, **kwargs: object) -> str:
            if self == global_file:
                raise PermissionError("global file unreadable")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", autospec=True, side_effect=read_text_with_global_error):
            result = resolver.resolve(["SECRET"], "prov")

        assert "SECRET" in result.missing


class TestSecretsResolverMissingInstructions:
    def test_get_missing_instructions_format(self) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        resolver = SecretsResolver(secrets_dir=Path("/tmp/test"))
        text = resolver.get_missing_instructions(["API_KEY", "DB_PASS"], "my-provider")
        assert "API_KEY" in text
        assert "DB_PASS" in text
        assert "my-provider" in text
        assert "export API_KEY" in text
        assert "echo 'your_value'" in text


class TestSecretsResolverDefaults:
    def test_default_secrets_dir(self) -> None:
        from mcp_hangar.application.services.secrets_resolver import SecretsResolver

        resolver = SecretsResolver()
        assert resolver.secrets_dir == Path.home() / ".config" / "mcp-hangar" / "secrets"


# ===================================================================
# 3. Auth Config -- parse_auth_config + get_default_auth_config
# ===================================================================


class TestParseAuthConfigNoneAndEmpty:
    def test_none_returns_defaults(self) -> None:
        from enterprise.auth.config import AuthConfig, parse_auth_config

        result = parse_auth_config(None)
        assert isinstance(result, AuthConfig)
        assert result.enabled is False

    def test_empty_dict_returns_defaults(self) -> None:
        from enterprise.auth.config import parse_auth_config

        result = parse_auth_config({})
        assert result.enabled is False
        assert result.allow_anonymous is False
        assert result.storage.driver == "memory"
        assert result.rate_limit.enabled is True
        assert result.api_key.enabled is True
        assert result.oidc.enabled is False
        assert result.opa.enabled is False
        assert result.role_assignments == []


class TestParseAuthConfigFull:
    def test_full_config_parses_all_sections(self) -> None:
        from enterprise.auth.config import parse_auth_config

        cfg = {
            "enabled": True,
            "allow_anonymous": True,
            "storage": {
                "driver": "postgresql",
                "path": "/custom/auth.db",
                "host": "db.local",
                "port": 5433,
                "database": "hangar",
                "user": "admin",
                "password": "s3cret",
                "min_connections": 5,
                "max_connections": 20,
            },
            "rate_limit": {
                "enabled": False,
                "max_attempts": 5,
                "window_seconds": 120,
                "lockout_seconds": 600,
            },
            "api_key": {
                "enabled": False,
                "header_name": "X-Custom-Key",
            },
            "oidc": {
                "enabled": True,
                "issuer": "https://auth.example.com",
                "audience": "hangar-api",
                "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
                "client_id": "my-client",
                "subject_claim": "user_id",
                "groups_claim": "roles",
                "tenant_claim": "org_id",
                "email_claim": "mail",
                "max_token_lifetime_seconds": 7200,
            },
            "opa": {
                "enabled": True,
                "url": "http://opa:8181",
                "policy_path": "v1/data/custom/allow",
                "timeout": 10.0,
            },
            "role_assignments": [
                {
                    "principal": "user:admin@co.com",
                    "role": "admin",
                    "scope": "global",
                },
                {
                    "principal": "group:devs",
                    "role": "developer",
                },
            ],
        }
        result = parse_auth_config(cfg)

        assert result.enabled is True
        assert result.allow_anonymous is True

        # Storage
        assert result.storage.driver == "postgresql"
        assert result.storage.path == "/custom/auth.db"
        assert result.storage.host == "db.local"
        assert result.storage.port == 5433
        assert result.storage.database == "hangar"
        assert result.storage.user == "admin"
        assert result.storage.password == "s3cret"
        assert result.storage.min_connections == 5
        assert result.storage.max_connections == 20

        # Rate limit
        assert result.rate_limit.enabled is False
        assert result.rate_limit.max_attempts == 5
        assert result.rate_limit.window_seconds == 120
        assert result.rate_limit.lockout_seconds == 600

        # API key
        assert result.api_key.enabled is False
        assert result.api_key.header_name == "X-Custom-Key"

        # OIDC
        assert result.oidc.enabled is True
        assert result.oidc.issuer == "https://auth.example.com"
        assert result.oidc.audience == "hangar-api"
        assert result.oidc.jwks_uri == "https://auth.example.com/.well-known/jwks.json"
        assert result.oidc.client_id == "my-client"
        assert result.oidc.subject_claim == "user_id"
        assert result.oidc.groups_claim == "roles"
        assert result.oidc.tenant_claim == "org_id"
        assert result.oidc.email_claim == "mail"
        assert result.oidc.max_token_lifetime_seconds == 7200

        # OPA
        assert result.opa.enabled is True
        assert result.opa.url == "http://opa:8181"
        assert result.opa.policy_path == "v1/data/custom/allow"
        assert result.opa.timeout == 10.0

        # Role assignments
        assert len(result.role_assignments) == 2
        assert result.role_assignments[0].principal == "user:admin@co.com"
        assert result.role_assignments[0].role == "admin"
        assert result.role_assignments[1].scope == "global"  # default


class TestParseAuthConfigEnvOverride:
    def test_jwt_max_token_lifetime_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from enterprise.auth.config import parse_auth_config

        monkeypatch.setenv("MCP_JWT_MAX_TOKEN_LIFETIME", "1800")
        result = parse_auth_config({"oidc": {"max_token_lifetime_seconds": 7200}})
        assert result.oidc.max_token_lifetime_seconds == 1800


class TestParseAuthConfigRoleAssignmentEdgeCases:
    def test_non_dict_entries_in_role_assignments_are_skipped(self) -> None:
        from enterprise.auth.config import parse_auth_config

        cfg = {
            "role_assignments": [
                {"principal": "user:a", "role": "admin"},
                "not-a-dict",
                42,
                None,
            ]
        }
        result = parse_auth_config(cfg)
        assert len(result.role_assignments) == 1
        assert result.role_assignments[0].principal == "user:a"


class TestGetDefaultAuthConfig:
    def test_returns_disabled_anonymous_allowed(self) -> None:
        from enterprise.auth.config import get_default_auth_config

        cfg = get_default_auth_config()
        assert cfg.enabled is False
        assert cfg.allow_anonymous is True


# ===================================================================
# 4. Roles stub fallbacks (domain/security/roles.py)
# ===================================================================


class TestRolesEnterpriseAvailable:
    """When enterprise is installed, the stub re-exports enterprise symbols."""

    def test_builtin_roles_populated(self) -> None:
        from mcp_hangar.domain.security.roles import BUILTIN_ROLES

        assert len(BUILTIN_ROLES) > 0
        assert "admin" in BUILTIN_ROLES

    def test_get_builtin_role_returns_role(self) -> None:
        from mcp_hangar.domain.security.roles import get_builtin_role

        role = get_builtin_role("admin")
        assert role is not None
        assert role.name == "admin"

    def test_get_builtin_role_unknown_returns_none(self) -> None:
        from mcp_hangar.domain.security.roles import get_builtin_role

        assert get_builtin_role("nonexistent") is None

    def test_get_permission_returns_permission(self) -> None:
        from mcp_hangar.domain.security.roles import get_permission

        perm = get_permission("provider:read")
        assert perm is not None
        assert perm.resource_type == "provider"
        assert perm.action == "read"

    def test_get_permission_unknown_returns_none(self) -> None:
        from mcp_hangar.domain.security.roles import get_permission

        assert get_permission("nonexistent:thing") is None

    def test_list_builtin_roles_returns_names(self) -> None:
        from mcp_hangar.domain.security.roles import list_builtin_roles

        names = list_builtin_roles()
        assert isinstance(names, list)
        assert "admin" in names

    def test_list_permissions_returns_keys(self) -> None:
        from mcp_hangar.domain.security.roles import list_permissions

        keys = list_permissions()
        assert isinstance(keys, list)
        assert "provider:read" in keys

    def test_role_constants_are_not_none(self) -> None:
        from mcp_hangar.domain.security.roles import (
            ROLE_ADMIN,
            ROLE_AUDITOR,
            ROLE_DEVELOPER,
            ROLE_PROVIDER_ADMIN,
            ROLE_VIEWER,
        )

        assert ROLE_ADMIN is not None
        assert ROLE_AUDITOR is not None
        assert ROLE_DEVELOPER is not None
        assert ROLE_PROVIDER_ADMIN is not None
        assert ROLE_VIEWER is not None

    def test_permissions_dict_populated(self) -> None:
        from mcp_hangar.domain.security.roles import PERMISSIONS

        assert len(PERMISSIONS) > 0
        assert "tool:invoke" in PERMISSIONS


# ===================================================================
# 5. JWTIdentityExtractor uncovered branches
# ===================================================================


class TestJWTExtractorNoneMetadata:
    def test_none_metadata_returns_none(self) -> None:
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        ext = JWTIdentityExtractor(secret_or_key="secret")
        assert ext.extract(None) is None


class TestJWTExtractorNoBearerPrefix:
    def test_no_authorization_header_returns_none(self) -> None:
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        ext = JWTIdentityExtractor(secret_or_key="secret")
        assert ext.extract({"content-type": "application/json"}) is None

    def test_non_bearer_auth_returns_none(self) -> None:
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        ext = JWTIdentityExtractor(secret_or_key="secret")
        assert ext.extract({"authorization": "Basic dXNlcjpwYXNz"}) is None


class TestJWTExtractorEmptyToken:
    def test_bearer_with_empty_token_returns_none(self) -> None:
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        ext = JWTIdentityExtractor(secret_or_key="secret")
        assert ext.extract({"authorization": "Bearer   "}) is None


class TestJWTExtractorPyJWTMissing:
    def test_missing_pyjwt_returns_none(self) -> None:
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        ext = JWTIdentityExtractor(secret_or_key="secret")
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "jwt":
                raise ImportError("no jwt")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            result = ext.extract({"authorization": "Bearer some.fake.token"})
        assert result is None


class TestJWTExtractorTokenDecoding:
    """Cover expired, bad audience, bad issuer, and generic invalid token errors."""

    def _make_extractor(self, **kwargs):
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        defaults = {"secret_or_key": "test-secret", "algorithms": ["HS256"]}
        defaults.update(kwargs)
        return JWTIdentityExtractor(**defaults)

    def _make_token(self, claims: dict, secret: str = "test-secret") -> str:
        import jwt

        return jwt.encode(claims, secret, algorithm="HS256")

    def test_expired_token_returns_none(self) -> None:
        import jwt as pyjwt

        ext = self._make_extractor()
        token = pyjwt.encode({"sub": "user1", "exp": 1}, "test-secret", algorithm="HS256")
        result = ext.extract({"authorization": f"Bearer {token}"})
        assert result is None

    def test_invalid_audience_returns_none(self) -> None:
        ext = self._make_extractor(audience="expected-audience")
        token = self._make_token({"sub": "user1", "aud": "wrong-audience"})
        result = ext.extract({"authorization": f"Bearer {token}"})
        assert result is None

    def test_invalid_issuer_returns_none(self) -> None:
        ext = self._make_extractor(issuer="expected-issuer")
        token = self._make_token({"sub": "user1", "iss": "wrong-issuer"})
        result = ext.extract({"authorization": f"Bearer {token}"})
        assert result is None

    def test_generic_invalid_token_returns_none(self) -> None:
        ext = self._make_extractor()
        result = ext.extract({"authorization": "Bearer not.a.valid.jwt"})
        assert result is None

    def test_valid_token_with_audience_and_issuer(self) -> None:
        ext = self._make_extractor(audience="my-aud", issuer="my-iss")
        token = self._make_token({"sub": "user1", "aud": "my-aud", "iss": "my-iss"})
        result = ext.extract({"authorization": f"Bearer {token}"})
        assert result is not None
        assert result.caller.user_id == "user1"


class TestJWTExtractorClaimsToContext:
    """Cover _claims_to_context branches: missing sub, invalid principal_type,
    agent_id/session_id present/absent, correlation_id."""

    def _make_extractor(self, **kwargs):
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )

        defaults = {"secret_or_key": "test-secret", "algorithms": ["HS256"]}
        defaults.update(kwargs)
        return JWTIdentityExtractor(**defaults)

    def _make_token(self, claims: dict) -> str:
        import jwt

        return jwt.encode(claims, "test-secret", algorithm="HS256")

    def test_missing_sub_returns_none(self) -> None:
        ext = self._make_extractor()
        token = self._make_token({"no_sub": "val"})
        assert ext.extract({"authorization": f"Bearer {token}"}) is None

    def test_invalid_principal_type_defaults_to_user(self) -> None:
        ext = self._make_extractor()
        token = self._make_token({"sub": "u1", "type": "robot"})
        ctx = ext.extract({"authorization": f"Bearer {token}"})
        assert ctx is not None
        assert ctx.caller.principal_type == "user"

    def test_service_principal_type(self) -> None:
        ext = self._make_extractor()
        token = self._make_token({"sub": "svc1", "type": "service"})
        ctx = ext.extract({"authorization": f"Bearer {token}"})
        assert ctx is not None
        assert ctx.caller.principal_type == "service"

    def test_agent_id_and_session_id_populated(self) -> None:
        ext = self._make_extractor()
        token = self._make_token({"sub": "u1", "agent_id": "a1", "sid": "s1", "jti": "corr1"})
        ctx = ext.extract({"authorization": f"Bearer {token}"})
        assert ctx is not None
        assert ctx.caller.agent_id == "a1"
        assert ctx.caller.session_id == "s1"
        assert ctx.correlation_id == "corr1"

    def test_no_agent_id_session_or_correlation(self) -> None:
        ext = self._make_extractor()
        token = self._make_token({"sub": "u1"})
        ctx = ext.extract({"authorization": f"Bearer {token}"})
        assert ctx is not None
        assert ctx.caller.agent_id is None
        assert ctx.caller.session_id is None
        assert ctx.correlation_id is None

    def test_caller_identity_construction_failure_returns_none(self) -> None:
        """CallerIdentity raises ValueError when principal_type is 'user'
        but user_id is None/empty. We simulate by passing sub='' which is falsy."""
        ext = self._make_extractor()
        token = self._make_token({"sub": ""})
        assert ext.extract({"authorization": f"Bearer {token}"}) is None


class TestJWTExtractorListMetadata:
    def test_list_of_tuples_metadata(self) -> None:
        from mcp_hangar.infrastructure.identity.jwt_extractor import (
            JWTIdentityExtractor,
        )
        import jwt

        ext = JWTIdentityExtractor(secret_or_key="secret", algorithms=["HS256"])
        token = jwt.encode({"sub": "user1"}, "secret", algorithm="HS256")
        metadata = [("Authorization", f"Bearer {token}")]
        ctx = ext.extract(metadata)
        assert ctx is not None
        assert ctx.caller.user_id == "user1"


# ===================================================================
# 6. OutputRedactor remaining edge cases
# ===================================================================


class TestRedactorLooksLikeCode:
    """Cover _looks_like_code branches at lines 231, 234, 236, 239."""

    def test_upper_snake_case_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        # ALL_UPPER_WITH_UNDERSCORES should be treated as code
        long_const = "THIS_IS_A_VERY_LONG_CONSTANT_NAME_FOR_TESTING"
        result = r.redact(long_const)
        assert long_const in result  # not redacted

    def test_get_prefix_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "get_very_long_function_name_that_is_over_thirty_two_chars"
        result = r.redact(value)
        assert value in result

    def test_set_prefix_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "set_very_long_function_name_that_is_over_thirty_two_chars"
        result = r.redact(value)
        assert value in result

    def test_test_suffix_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "some_very_long_identifier_name_test"
        result = r.redact(value)
        assert value in result

    def test_spec_suffix_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "some_very_long_identifier_name_spec"
        result = r.redact(value)
        assert value in result

    def test_test_prefix_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "test_very_long_function_name_over_thirty_two"
        result = r.redact(value)
        assert value in result

    def test_spec_prefix_treated_as_code(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "spec_very_long_function_name_over_thirty_two"
        result = r.redact(value)
        assert value in result


class TestRedactorKnownSafePattern:
    """Cover _is_known_safe_pattern branches at lines 262, 265, 268."""

    def test_sha256_prefix_is_safe(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "sha256-abcdef1234567890abcdef1234567890ab"
        result = r.redact(value)
        assert value in result

    def test_uuid_prefix_is_safe(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        value = "uuid-12345678901234567890123456789012"
        result = r.redact(value)
        assert value in result

    def test_low_entropy_string_is_safe(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        # Only uses 'a' and 'b' -- 2 unique chars <= 3
        value = "a" * 32 + "b"
        result = r.redact(value)
        assert value in result

    def test_not_safe_string_is_redacted(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=True, min_long_string_length=32)
        # High entropy, no safe prefix, not code-like
        value = "aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3"
        result = r.redact(value)
        assert "[REDACTED:potential_secret]" in result


class TestRedactorIsSensitive:
    """Cover is_sensitive branches at lines 279-292."""

    def test_empty_text_not_sensitive(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(known_secrets={"key": "supersecret"})
        assert r.is_sensitive("") is False

    def test_known_secret_detected(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(known_secrets={"key": "supersecret"})
        assert r.is_sensitive("the supersecret value") is True

    def test_builtin_pattern_detected(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        assert r.is_sensitive("ghp_" + "a" * 40) is True

    def test_custom_pattern_detected(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        r.add_pattern(r"CUSTOM_[A-Z]{10,}", "custom_secret")
        assert r.is_sensitive("CUSTOM_ABCDEFGHIJ") is True

    def test_safe_text_not_sensitive(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        assert r.is_sensitive("just some normal text") is False


class TestRedactorCustomPatterns:
    def test_add_pattern_with_string(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=False)
        r.add_pattern(r"MY_TOKEN_[a-z]+", "my_token")
        result = r.redact("here is MY_TOKEN_abcdef")
        assert "[REDACTED:my_token]" in result

    def test_add_pattern_with_compiled_regex(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=False)
        r.add_pattern(re.compile(r"CUST_[0-9]+"), "cust_id", "CUST_[HIDDEN]")
        result = r.redact("found CUST_12345")
        assert "CUST_[HIDDEN]" in result

    def test_custom_pattern_with_replacement(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=False)
        r.add_pattern(r"pw=[^ ]+", "password", "pw=[HIDDEN]")
        result = r.redact("login pw=mysecretpass done")
        assert "pw=[HIDDEN]" in result


class TestRedactorAddKnownSecret:
    def test_short_secret_ignored(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        r.add_known_secret("short", "ab")
        assert "short" not in r._known_secrets

    def test_empty_secret_ignored(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        r.add_known_secret("empty", "")
        assert "empty" not in r._known_secrets

    def test_valid_secret_added(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        r.add_known_secret("valid", "abcdefgh")
        assert r._known_secrets["valid"] == "abcdefgh"


class TestRedactorLongStringDisabled:
    def test_no_long_string_redaction_when_disabled(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor(redact_long_strings=False)
        value = "aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3"
        result = r.redact(value)
        assert value in result


class TestRedactorRedactsEmptyText:
    def test_empty_text_returns_empty(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        assert r.redact("") == ""

    def test_none_like_empty_string(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        r = OutputRedactor()
        assert r.redact("") == ""


class TestRedactorKnownSecretInLongString:
    """Line 212: if value in self._known_secrets.values() -- long string that
    matches a known secret should NOT be redacted as potential_secret."""

    def test_known_secret_value_not_double_redacted(self) -> None:
        from mcp_hangar.domain.security.redactor import OutputRedactor

        secret_val = "aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV"
        r = OutputRedactor(
            known_secrets={"my_key": secret_val},
            redact_long_strings=True,
            min_long_string_length=32,
        )
        # The known secret redaction replaces the value first,
        # so long-string redaction won't see the original value
        result = r.redact(f"value is {secret_val}")
        assert "[REDACTED:my_key]" in result
