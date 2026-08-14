"""Auth bootstrap: which storage backend a config selects, and what it wires up."""

from __future__ import annotations

from typing import Any
from unittest.mock import ANY, MagicMock, patch

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


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


def _make_auth_request(headers: dict[str, str] | None = None, source_ip: str = "127.0.0.1") -> AuthRequest:
    return AuthRequest(
        headers=headers or {},
        source_ip=source_ip,
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


class TestCreateStorageBackendsMemory:
    """Test _create_storage_backends with memory driver."""

    def test_memory_driver(self):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        config = AuthConfig(storage=StorageConfig(driver="memory"))
        api_key_store, role_store, tap_store = _create_storage_backends(config)

        assert isinstance(api_key_store, InMemoryApiKeyStore)
        assert isinstance(role_store, InMemoryRoleStore)
        assert tap_store is None


class TestCreateStorageBackendsEventSourcing:
    """Test _create_storage_backends with event_sourcing driver."""

    def test_event_sourcing_without_event_store_raises(self):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="event_sourcing"))
        with pytest.raises(ValueError, match="requires event_store"):
            _create_storage_backends(config, event_store=None)

    def test_event_sourcing_with_event_store(self):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="event_sourcing"))
        mock_event_store = MagicMock()
        mock_event_bus = MagicMock()

        api_key_store, role_store, tap_store = _create_storage_backends(
            config,
            event_store=mock_event_store,
            event_bus=mock_event_bus,
        )
        assert tap_store is None
        # Stores should be EventSourced instances
        assert api_key_store is not None
        assert role_store is not None


class TestCreateStorageBackendsSqlite:
    """Test _create_storage_backends with sqlite driver."""

    def test_sqlite_driver(self, tmp_path):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig

        db_path = tmp_path / "auth" / "test.db"
        config = AuthConfig(storage=StorageConfig(driver="sqlite", path=str(db_path)))

        api_key_store, role_store, tap_store = _create_storage_backends(config)
        assert api_key_store is not None
        assert role_store is not None
        assert tap_store is not None


class TestCreateStorageBackendsPostgres:
    """Test _create_storage_backends with postgresql driver."""

    def test_postgres_driver(self):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(
            storage=StorageConfig(
                driver="postgresql",
                host="localhost",
                port=5432,
                database="testdb",
                user="testuser",
                password="testpass",
            )
        )

        # Patch the lazy imports for postgres stores
        with (
            patch("mcp_hangar.infrastructure.persistence.database_common.PostgresConnectionFactory") as mock_factory,
            patch("mcp_hangar.auth.infrastructure.postgres_store.PostgresApiKeyStore") as mock_key_store_cls,
            patch("mcp_hangar.auth.infrastructure.postgres_store.PostgresRoleStore") as mock_role_store_cls,
        ):
            mock_factory.return_value = MagicMock()
            mock_key_instance = MagicMock()
            mock_role_instance = MagicMock()
            mock_key_store_cls.return_value = mock_key_instance
            mock_role_store_cls.return_value = mock_role_instance

            api_key_store, role_store, tap_store = _create_storage_backends(config)

            mock_factory.assert_called_once()
            mock_key_instance.initialize.assert_called_once()
            mock_role_instance.initialize.assert_called_once()
            # This used to assert `tap_store is None`, which pinned the defect
            # rather than the behaviour: it made postgresql the only durable
            # driver that served no tool-access policies, and a gateway
            # configured that way died at startup on
            # `relation "tool_access_policies" does not exist`. The sqlite
            # branch always built its own.
            assert tap_store is not None

    def test_postgres_alias(self):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="postgres"))

        with (
            patch("mcp_hangar.infrastructure.persistence.database_common.PostgresConnectionFactory") as mock_factory,
            patch("mcp_hangar.auth.infrastructure.postgres_store.PostgresApiKeyStore") as mock_key_store_cls,
            patch("mcp_hangar.auth.infrastructure.postgres_store.PostgresRoleStore") as mock_role_store_cls,
        ):
            mock_factory.return_value = MagicMock()
            mock_key_store_cls.return_value = MagicMock()
            mock_role_store_cls.return_value = MagicMock()

            api_key_store, role_store, tap_store = _create_storage_backends(config)
            assert api_key_store is not None


class TestCreateStorageBackendsUnknown:
    """Test _create_storage_backends with unknown driver."""

    def test_unknown_driver_raises(self):
        from mcp_hangar.auth.bootstrap import _create_storage_backends
        from mcp_hangar.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="redis"))
        with pytest.raises(ValueError, match="Unknown auth storage driver"):
            _create_storage_backends(config)


class TestAuthComponents:
    """Test AuthComponents class."""

    def test_enabled_with_authenticators(self):
        from mcp_hangar.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authn._authenticators = [MagicMock()]
        authn._allow_anonymous = False
        authz = MagicMock()

        components = AuthComponents(authn_middleware=authn, authz_middleware=authz)
        assert components.enabled is True

    def test_enabled_with_no_authenticators_but_not_anonymous(self):
        from mcp_hangar.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authn._authenticators = []
        authn._allow_anonymous = False
        authz = MagicMock()

        components = AuthComponents(authn_middleware=authn, authz_middleware=authz)
        assert components.enabled is True

    def test_not_enabled_when_empty_authenticators_and_anonymous(self):
        from mcp_hangar.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authn._authenticators = []
        authn._allow_anonymous = True
        authz = MagicMock()

        components = AuthComponents(authn_middleware=authn, authz_middleware=authz)
        assert components.enabled is False

    def test_stores_accessible(self):
        from mcp_hangar.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authz = MagicMock()
        key_store = MagicMock()
        role_store = MagicMock()
        tap_store = MagicMock()

        components = AuthComponents(
            authn_middleware=authn,
            authz_middleware=authz,
            api_key_store=key_store,
            role_store=role_store,
            tap_store=tap_store,
        )
        assert components.api_key_store is key_store
        assert components.role_store is role_store
        assert components.tap_store is tap_store


class TestNullAuthComponents:
    """Test NullAuthComponents."""

    def test_enabled_returns_false(self):
        from mcp_hangar.auth.bootstrap import NullAuthComponents

        null = NullAuthComponents()
        assert null.enabled is False

    def test_authn_returns_system_principal(self):
        from mcp_hangar.auth.bootstrap import NullAuthComponents

        null = NullAuthComponents()
        # The NullAuthenticator inside should return system principal
        request = _make_auth_request({"Authorization": "Bearer fake"})
        principal = null.authn_middleware._authenticators[0].authenticate(request)
        assert principal.type.value == "system"

    def test_authz_allows_all(self):
        from mcp_hangar.auth.bootstrap import NullAuthComponents

        null = NullAuthComponents()
        request = _make_authz_request()
        result = null.authz_middleware._authorizer.authorize(request)
        assert result.allowed
        assert "auth_disabled" in result.reason


class TestReplayTapPolicies:
    """Test _replay_tap_policies function."""

    # ToolAccessPolicy is what the store hands back now: a whole policy, not the
    # two lists a caller has to remember to widen (#915).

    def test_replay_provider_scope(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("provider", "my-provider", ToolAccessPolicy(allow_list=("tool_a", "tool_b"), deny_list=("tool_c",))),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_mcp_server_policy.assert_called_once()

    def test_replay_group_scope(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("group", "my-group", ToolAccessPolicy(allow_list=("tool_x",))),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_group_policy.assert_called_once()

    def test_replay_member_scope_with_colon(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("member", "group1:member1", ToolAccessPolicy(allow_list=("tool_y",))),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_member_policy.assert_called_once_with("group1", "member1", ANY)

    def test_replay_member_scope_without_colon(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("member", "standalone", ToolAccessPolicy(allow_list=("tool_z",))),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_member_policy.assert_called_once_with("standalone", "standalone", ANY)

    def test_replay_exception_does_not_abort(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("provider", "p1", ToolAccessPolicy(allow_list=("tool_a",))),
            ("provider", "p2", ToolAccessPolicy(allow_list=("tool_b",))),
        ]

        mock_resolver = MagicMock()
        mock_resolver.set_mcp_server_policy.side_effect = [RuntimeError("fail"), None]

        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            # Should not raise -- fault barrier
            _replay_tap_policies(mock_tap_store)
            assert mock_resolver.set_mcp_server_policy.call_count == 2

    def test_resolver_none_skips(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()

        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=None,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_tap_store.list_all_policies.assert_not_called()

    def test_import_error_fallback(self):
        from mcp_hangar.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = []

        mock_resolver = MagicMock()
        # Simulate get_tool_access_resolver raising ImportError at import time
        # by patching the module attribute to raise when called
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            side_effect=ImportError("no such function"),
        ):
            with patch.object(
                __import__("mcp_hangar.domain.services.tool_access_resolver", fromlist=["_resolver"]),
                "_resolver",
                mock_resolver,
                create=True,
            ):
                _replay_tap_policies(mock_tap_store)


class TestBootstrapAuth:
    """Test bootstrap_auth function."""

    def test_disabled_config_returns_null_components(self):
        from mcp_hangar.auth.bootstrap import NullAuthComponents, bootstrap_auth
        from mcp_hangar.auth.config import AuthConfig

        config = AuthConfig(enabled=False)
        result = bootstrap_auth(config)
        assert isinstance(result, NullAuthComponents)
        assert result.enabled is False

    def test_enabled_with_api_key_auth(self):
        from mcp_hangar.auth.bootstrap import AuthComponents, bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=True, header_name="X-API-Key"),
        )
        result = bootstrap_auth(config)
        assert isinstance(result, AuthComponents)
        assert result.enabled is True
        assert len(result.authn_middleware._authenticators) >= 1

    def test_enabled_with_oidc_auth_incomplete_config_warns(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, OIDCAuthConfig, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            oidc=OIDCAuthConfig(enabled=True, issuer="", audience=""),  # incomplete
        )
        result = bootstrap_auth(config)
        # OIDC should NOT be added due to incomplete config
        # Only API key auth (disabled) and no OIDC -- 0 authenticators
        authenticator_count = len(result.authn_middleware._authenticators)
        assert authenticator_count == 0

    def test_enabled_with_oidc_complete_config(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, OIDCAuthConfig, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            oidc=OIDCAuthConfig(enabled=True, issuer="https://auth.example.com", audience="my-api"),
        )
        result = bootstrap_auth(config)
        assert len(result.authn_middleware._authenticators) == 1

    def test_role_assignments_from_config(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[
                RoleAssignment(principal="user:admin", role="admin", scope="global"),
            ],
        )
        result = bootstrap_auth(config)
        # Should have assigned the admin role
        roles = result.role_store.get_roles_for_principal("user:admin")
        assert len(roles) >= 1

    def test_invalid_role_assignment_skipped(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[
                RoleAssignment(principal="", role="admin"),  # invalid: empty principal
                RoleAssignment(principal="user:x", role=""),  # invalid: empty role
            ],
        )
        # Should not raise
        result = bootstrap_auth(config)
        assert result is not None

    def test_opa_enabled_wraps_with_combined_authorizer(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, OPAConfig, StorageConfig
        from mcp_hangar.auth.infrastructure.opa_authorizer import CombinedAuthorizer

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            opa=OPAConfig(enabled=True, url="http://localhost:8181"),
        )
        result = bootstrap_auth(config)
        # The authorizer inside authz_middleware should be a CombinedAuthorizer
        assert isinstance(result.authz_middleware._authorizer, CombinedAuthorizer)

    def test_rate_limiter_disabled(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RateLimitConfig, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            rate_limit=RateLimitConfig(enabled=False),
        )
        result = bootstrap_auth(config)
        # Rate limiter should not be passed to middleware
        assert result.authn_middleware._rate_limiter is None

    def test_rate_limiter_enabled(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RateLimitConfig, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            rate_limit=RateLimitConfig(enabled=True, max_attempts=5, window_seconds=30),
        )
        result = bootstrap_auth(config)
        assert result.authn_middleware._rate_limiter is not None

    def test_tap_store_replay_called_when_present(self, tmp_path):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, StorageConfig

        db_path = tmp_path / "auth" / "test.db"
        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="sqlite", path=str(db_path)),
            api_key=ApiKeyAuthConfig(enabled=False),
        )

        with patch("mcp_hangar.auth.bootstrap._replay_tap_policies") as mock_replay:
            result = bootstrap_auth(config)
            # tap_store should be non-None for sqlite driver
            assert result.tap_store is not None
            mock_replay.assert_called_once_with(result.tap_store)

    def test_role_assignment_value_error_logged(self):
        from mcp_hangar.auth.bootstrap import bootstrap_auth
        from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[
                RoleAssignment(principal="user:x", role="admin"),
            ],
        )

        # Make assign_role raise ValueError
        with patch(
            "mcp_hangar.auth.infrastructure.rbac_authorizer.InMemoryRoleStore.assign_role",
            side_effect=ValueError("test error"),
        ):
            # Should not raise -- logged and continued
            result = bootstrap_auth(config)
            assert result is not None
