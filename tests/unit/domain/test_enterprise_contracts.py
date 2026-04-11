"""Tests for enterprise contract Null implementations.

Verifies that every enterprise contract (Protocol/ABC) in
domain.contracts and application.ports has a working Null implementation
that satisfies its interface and returns the expected no-op values.

Contract inventory (6 enterprise contracts + 4 pre-existing):
    1. IAuthenticator -> NullAuthenticator
    2. IApiKeyStore -> NullApiKeyStore
    3. IAuthorizer -> NullAuthorizer
    4. IRoleStore -> NullRoleStore
    5. IToolAccessPolicyStore -> NullToolAccessPolicyStore
    6. IToolAccessPolicyEnforcer -> NullToolAccessPolicyEnforcer
    7. IEventStore -> NullEventStore (pre-existing)
    8. IDurableEventStore extends IEventStore (pre-existing, hierarchy check)
    9. ObservabilityPort -> NullObservabilityAdapter (pre-existing)
   10. IAuditExporter -> NullAuditExporter (pre-existing)
"""

import pytest

from mcp_hangar.domain.contracts.authentication import (
    AuthRequest,
    IApiKeyStore,
    IAuthenticator,
    NullApiKeyStore,
    NullAuthenticator,
)
from mcp_hangar.domain.contracts.authorization import (
    AuthorizationRequest,
    AuthorizationResult,
    IAuthorizer,
    IRoleStore,
    IToolAccessPolicyEnforcer,
    IToolAccessPolicyStore,
    NullAuthorizer,
    NullRoleStore,
    NullToolAccessPolicyEnforcer,
    NullToolAccessPolicyStore,
    PolicyEvaluationResult,
)
from mcp_hangar.domain.contracts.event_store import (
    IDurableEventStore,
    IEventStore,
    NullEventStore,
)
from mcp_hangar.application.ports.observability import (
    IAuditExporter,
    NullAuditExporter,
    NullObservabilityAdapter,
    ObservabilityPort,
)
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType


# -- Fixtures --


def _make_auth_request() -> AuthRequest:
    """Create a minimal AuthRequest for testing."""
    return AuthRequest(headers={}, source_ip="127.0.0.1")


def _make_principal() -> Principal:
    """Create a test Principal."""
    return Principal(id=PrincipalId("test-user"), type=PrincipalType.USER)


def _make_authorization_request(principal: Principal | None = None) -> AuthorizationRequest:
    """Create a minimal AuthorizationRequest for testing."""
    return AuthorizationRequest(
        principal=principal or _make_principal(),
        action="read",
        resource_type="provider",
        resource_id="test",
    )


# -- IAuthenticator / NullAuthenticator --


class TestNullAuthenticator:
    def test_satisfies_iauthenticator_protocol(self) -> None:
        authenticator = NullAuthenticator()
        assert isinstance(authenticator, IAuthenticator)

    def test_authenticate_returns_principal_with_system_type(self) -> None:
        authenticator = NullAuthenticator()
        principal = authenticator.authenticate(_make_auth_request())
        assert isinstance(principal, Principal)
        assert principal.type == PrincipalType.SYSTEM

    def test_authenticate_returns_anonymous_principal_id(self) -> None:
        authenticator = NullAuthenticator()
        principal = authenticator.authenticate(_make_auth_request())
        assert principal.id.value == "anonymous"

    def test_supports_returns_true_for_any_request(self) -> None:
        authenticator = NullAuthenticator()
        assert authenticator.supports(_make_auth_request()) is True

    def test_supports_returns_true_for_request_with_headers(self) -> None:
        authenticator = NullAuthenticator()
        request = AuthRequest(headers={"Authorization": "Bearer token"}, source_ip="10.0.0.1")
        assert authenticator.supports(request) is True


# -- IApiKeyStore / NullApiKeyStore --


class TestNullApiKeyStore:
    def test_satisfies_iapikeystore_protocol(self) -> None:
        store = NullApiKeyStore()
        assert isinstance(store, IApiKeyStore)

    def test_get_principal_for_key_returns_none(self) -> None:
        store = NullApiKeyStore()
        assert store.get_principal_for_key("some-hash") is None

    def test_list_keys_returns_empty_list(self) -> None:
        store = NullApiKeyStore()
        assert store.list_keys("some-principal") == []

    def test_count_keys_returns_zero(self) -> None:
        store = NullApiKeyStore()
        assert store.count_keys("some-principal") == 0

    def test_create_key_raises_not_implemented_error(self) -> None:
        store = NullApiKeyStore()
        with pytest.raises(NotImplementedError, match="enterprise"):
            store.create_key("principal", "key-name")

    def test_rotate_key_raises_not_implemented_error(self) -> None:
        store = NullApiKeyStore()
        with pytest.raises(NotImplementedError, match="enterprise"):
            store.rotate_key("key-id")

    def test_revoke_key_returns_false(self) -> None:
        store = NullApiKeyStore()
        assert store.revoke_key("some-key-id") is False


# -- IAuthorizer / NullAuthorizer --


class TestNullAuthorizer:
    def test_satisfies_iauthorizer_protocol(self) -> None:
        authorizer = NullAuthorizer()
        assert isinstance(authorizer, IAuthorizer)

    def test_authorize_returns_allowed_true(self) -> None:
        authorizer = NullAuthorizer()
        result = authorizer.authorize(_make_authorization_request())
        assert isinstance(result, AuthorizationResult)
        assert result.allowed is True

    def test_authorize_includes_descriptive_reason(self) -> None:
        authorizer = NullAuthorizer()
        result = authorizer.authorize(_make_authorization_request())
        assert "null" in result.reason.lower() or "no rbac" in result.reason.lower()


# -- IRoleStore / NullRoleStore --


class TestNullRoleStore:
    def test_satisfies_irolestore_protocol(self) -> None:
        store = NullRoleStore()
        assert isinstance(store, IRoleStore)

    def test_get_role_returns_none(self) -> None:
        store = NullRoleStore()
        assert store.get_role("admin") is None

    def test_get_roles_for_principal_returns_empty_list(self) -> None:
        store = NullRoleStore()
        assert store.get_roles_for_principal("user-1") == []

    def test_list_all_roles_returns_empty_list(self) -> None:
        store = NullRoleStore()
        assert store.list_all_roles() == []

    def test_update_role_raises_not_implemented_error(self) -> None:
        store = NullRoleStore()
        with pytest.raises(NotImplementedError, match="enterprise"):
            store.update_role("admin", [], None)

    def test_assign_role_is_silent_noop(self) -> None:
        store = NullRoleStore()
        # Should not raise
        store.assign_role("user-1", "admin")

    def test_revoke_role_is_silent_noop(self) -> None:
        store = NullRoleStore()
        # Should not raise
        store.revoke_role("user-1", "admin")

    def test_delete_role_is_silent_noop(self) -> None:
        store = NullRoleStore()
        # Should not raise
        store.delete_role("admin")


# -- IToolAccessPolicyStore / NullToolAccessPolicyStore --


class TestNullToolAccessPolicyStore:
    def test_satisfies_itoolaccesspolicystore_protocol(self) -> None:
        store = NullToolAccessPolicyStore()
        assert isinstance(store, IToolAccessPolicyStore)

    def test_get_policy_returns_none(self) -> None:
        store = NullToolAccessPolicyStore()
        assert store.get_policy("provider", "math") is None

    def test_list_all_policies_returns_empty_list(self) -> None:
        store = NullToolAccessPolicyStore()
        assert store.list_all_policies() == []

    def test_set_policy_is_silent_noop(self) -> None:
        store = NullToolAccessPolicyStore()
        # Should not raise
        store.set_policy("provider", "math", ["add"], ["delete"])

    def test_clear_policy_is_silent_noop(self) -> None:
        store = NullToolAccessPolicyStore()
        # Should not raise
        store.clear_policy("provider", "math")


# -- IToolAccessPolicyEnforcer / NullToolAccessPolicyEnforcer --


class TestNullToolAccessPolicyEnforcer:
    def test_satisfies_itoolaccesspolicyenforcer_protocol(self) -> None:
        enforcer = NullToolAccessPolicyEnforcer()
        assert isinstance(enforcer, IToolAccessPolicyEnforcer)

    def test_evaluate_returns_allowed_true(self) -> None:
        enforcer = NullToolAccessPolicyEnforcer()
        result = enforcer.evaluate(
            principal=_make_principal(),
            provider_id="math",
            tool_name="add",
        )
        assert isinstance(result, PolicyEvaluationResult)
        assert result.allowed is True

    def test_evaluate_with_context_returns_allowed(self) -> None:
        enforcer = NullToolAccessPolicyEnforcer()
        result = enforcer.evaluate(
            principal=_make_principal(),
            provider_id="math",
            tool_name="divide",
            context={"group": "default"},
        )
        assert result.allowed is True


# -- IDurableEventStore / IEventStore hierarchy --


class TestEventStoreHierarchy:
    def test_idurable_event_store_is_subclass_of_ieventstore(self) -> None:
        assert issubclass(IDurableEventStore, IEventStore)

    def test_null_event_store_satisfies_ieventstore(self) -> None:
        store = NullEventStore()
        assert isinstance(store, IEventStore)

    def test_null_event_store_append_returns_incremented_version(self) -> None:
        store = NullEventStore()
        version = store.append("stream-1", [], -1)
        assert version == -1  # -1 + 0 events = -1

    def test_null_event_store_read_stream_returns_empty(self) -> None:
        store = NullEventStore()
        assert store.read_stream("stream-1") == []

    def test_null_event_store_get_stream_version_returns_minus_one(self) -> None:
        store = NullEventStore()
        assert store.get_stream_version("stream-1") == -1

    def test_null_event_store_list_streams_returns_empty(self) -> None:
        store = NullEventStore()
        assert store.list_streams() == []

    def test_null_event_store_load_snapshot_returns_none(self) -> None:
        store = NullEventStore()
        assert store.load_snapshot("stream-1") is None


# -- ObservabilityPort / NullObservabilityAdapter (pre-existing, regression) --


class TestNullObservabilityAdapter:
    def test_satisfies_observability_port(self) -> None:
        adapter = NullObservabilityAdapter()
        assert isinstance(adapter, ObservabilityPort)

    def test_start_tool_span_returns_span_handle(self) -> None:
        adapter = NullObservabilityAdapter()
        handle = adapter.start_tool_span("math", "add", {"a": 1})
        assert handle is not None
        # Should not raise
        handle.end_success({"result": 2})

    def test_flush_and_shutdown_are_noops(self) -> None:
        adapter = NullObservabilityAdapter()
        # Should not raise
        adapter.flush()
        adapter.shutdown()


# -- IAuditExporter / NullAuditExporter (pre-existing, regression) --


class TestNullAuditExporter:
    def test_null_audit_exporter_has_required_methods(self) -> None:
        """Verify NullAuditExporter implements the IAuditExporter interface methods."""
        exporter = NullAuditExporter()
        # Verify method existence and callability (IAuditExporter is not runtime_checkable)
        assert hasattr(exporter, "export_tool_invocation")
        assert hasattr(exporter, "export_provider_state_change")
        assert callable(exporter.export_tool_invocation)
        assert callable(exporter.export_provider_state_change)

    def test_export_tool_invocation_is_noop(self) -> None:
        exporter = NullAuditExporter()
        # Should not raise
        exporter.export_tool_invocation(
            provider_id="math",
            tool_name="add",
            status="success",
            duration_ms=10.0,
        )

    def test_export_provider_state_change_is_noop(self) -> None:
        exporter = NullAuditExporter()
        # Should not raise
        exporter.export_provider_state_change(
            provider_id="math",
            from_state="COLD",
            to_state="INITIALIZING",
        )


# -- Complete inventory check --


class TestEnterpriseContractInventory:
    def test_all_six_enterprise_contracts_have_null_implementations(self) -> None:
        """Verify the complete set of 6 enterprise contracts each have Null implementations."""
        enterprise_contract_pairs = [
            (IAuthenticator, NullAuthenticator, "authentication"),
            (IApiKeyStore, NullApiKeyStore, "authentication"),
            (IAuthorizer, NullAuthorizer, "authorization"),
            (IRoleStore, NullRoleStore, "authorization"),
            (IToolAccessPolicyStore, NullToolAccessPolicyStore, "authorization"),
            (IToolAccessPolicyEnforcer, NullToolAccessPolicyEnforcer, "authorization"),
        ]

        for contract, null_impl, module_name in enterprise_contract_pairs:
            instance = null_impl()
            assert isinstance(instance, contract), (
                f"Null implementation {null_impl.__name__} does not satisfy {contract.__name__} from {module_name}"
            )

        assert len(enterprise_contract_pairs) == 6, "Expected exactly 6 enterprise contract pairs"

    def test_pre_existing_contracts_have_null_implementations(self) -> None:
        """Verify pre-existing contracts also have working Null implementations."""
        # IEventStore (ABC-based)
        assert isinstance(NullEventStore(), IEventStore)
        # ObservabilityPort (ABC-based)
        assert isinstance(NullObservabilityAdapter(), ObservabilityPort)
        # IAuditExporter (Protocol, not runtime_checkable -- check methods)
        exporter = NullAuditExporter()
        assert hasattr(exporter, "export_tool_invocation")
        assert hasattr(exporter, "export_provider_state_change")
