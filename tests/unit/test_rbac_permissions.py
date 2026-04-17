# pyright: reportAny=false, reportUnknownVariableType=false

from enterprise.auth.roles import ROLE_ADMIN, ROLE_AGENT, ROLE_DEVELOPER, ROLE_VIEWER
from mcp_hangar.domain.value_objects.security import (
    PERMISSION_CONFIG_RELOAD,
    PERMISSION_POLICY_WRITE,
    PERMISSION_PROVIDERS_LIFECYCLE,
    PERMISSION_PROVIDERS_READ,
    PERMISSION_PROVIDERS_WRITE,
)


def test_granular_permission_constants_exist() -> None:
    assert PERMISSION_PROVIDERS_READ.resource_type == "providers"
    assert PERMISSION_PROVIDERS_READ.action == "read"
    assert PERMISSION_PROVIDERS_WRITE.resource_type == "providers"
    assert PERMISSION_PROVIDERS_WRITE.action == "write"
    assert PERMISSION_PROVIDERS_LIFECYCLE.resource_type == "providers"
    assert PERMISSION_PROVIDERS_LIFECYCLE.action == "lifecycle"
    assert PERMISSION_POLICY_WRITE.resource_type == "policy"
    assert PERMISSION_POLICY_WRITE.action == "write"
    assert PERMISSION_CONFIG_RELOAD.resource_type == "config"
    assert PERMISSION_CONFIG_RELOAD.action == "reload"


def test_admin_role_includes_all_granular_permissions() -> None:
    assert ROLE_ADMIN.has_permission("providers", "read")
    assert ROLE_ADMIN.has_permission("providers", "write")
    assert ROLE_ADMIN.has_permission("providers", "lifecycle")
    assert ROLE_ADMIN.has_permission("policy", "write")
    assert ROLE_ADMIN.has_permission("config", "reload")


def test_developer_role_has_provider_read_write_and_lifecycle() -> None:
    assert ROLE_DEVELOPER.has_permission("providers", "read")
    assert ROLE_DEVELOPER.has_permission("providers", "write")
    assert ROLE_DEVELOPER.has_permission("providers", "lifecycle")
    assert not ROLE_DEVELOPER.has_permission("policy", "write")
    assert not ROLE_DEVELOPER.has_permission("config", "reload")


def test_viewer_role_is_read_only_for_provider_api() -> None:
    assert ROLE_VIEWER.has_permission("providers", "read")
    assert not ROLE_VIEWER.has_permission("providers", "write")
    assert not ROLE_VIEWER.has_permission("providers", "lifecycle")


def test_agent_role_has_policy_write_only() -> None:
    assert ROLE_AGENT.has_permission("policy", "write")
    assert not ROLE_AGENT.has_permission("providers", "read")
    assert not ROLE_AGENT.has_permission("providers", "write")
    assert not ROLE_AGENT.has_permission("providers", "lifecycle")
