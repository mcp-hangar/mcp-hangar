"""Tests for Tool Access Policy CQRS handlers."""

from unittest.mock import Mock, patch

import pytest

from enterprise.auth.commands.commands import (
    ClearToolAccessPolicyCommand,
    SetToolAccessPolicyCommand,
)
from enterprise.auth.commands.handlers import (
    ClearToolAccessPolicyHandler,
    SetToolAccessPolicyHandler,
)
from enterprise.auth.queries.queries import (
    GetToolAccessPolicyQuery,
    ListAllRolesQuery,
    ListPrincipalsQuery,
)
from enterprise.auth.queries.handlers import (
    GetToolAccessPolicyHandler,
    ListAllRolesHandler,
    ListPrincipalsHandler,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore


# ---------------------------------------------------------------------------
# SetToolAccessPolicyHandler
# ---------------------------------------------------------------------------


class TestSetToolAccessPolicyHandler:
    """Tests for SetToolAccessPolicyHandler."""

    @pytest.fixture
    def tap_store(self):
        return Mock()

    @pytest.fixture
    def event_bus(self):
        return Mock()

    @pytest.fixture
    def handler(self, tap_store, event_bus):
        return SetToolAccessPolicyHandler(tap_store, event_bus)

    def test_returns_confirmation(self, handler, tap_store):
        """handle() returns set=True confirmation dict."""
        tap_store.set_policy.return_value = None

        with patch("mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver") as mock_resolver_fn:
            mock_resolver = Mock()
            mock_resolver_fn.return_value = mock_resolver

            command = SetToolAccessPolicyCommand(
                scope="provider",
                target_id="math",
                allow_list=["add", "sub"],
                deny_list=[],
            )
            result = handler.handle(command)

        assert result["set"] is True
        assert result["scope"] == "provider"
        assert result["target_id"] == "math"
        assert result["allow_list"] == ["add", "sub"]

    def test_calls_tap_store_set_policy(self, handler, tap_store):
        """handle() persists the policy to tap_store."""
        with patch("mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver"):
            command = SetToolAccessPolicyCommand(
                scope="provider",
                target_id="calc",
                allow_list=["add"],
                deny_list=["rm"],
            )
            handler.handle(command)

        tap_store.set_policy.assert_called_once_with(
            scope="provider",
            target_id="calc",
            allow_list=["add"],
            deny_list=["rm"],
        )

    def test_provider_scope_calls_set_provider_policy(self, handler, tap_store):
        """Provider scope routes to resolver.set_provider_policy."""
        with patch("mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver") as mock_fn:
            mock_resolver = Mock()
            mock_fn.return_value = mock_resolver

            command = SetToolAccessPolicyCommand(scope="provider", target_id="math", allow_list=["add"], deny_list=[])
            handler.handle(command)

        mock_resolver.set_provider_policy.assert_called_once()
        args = mock_resolver.set_provider_policy.call_args[0]
        assert args[0] == "math"
        assert isinstance(args[1], ToolAccessPolicy)

    def test_group_scope_calls_set_group_policy(self, handler, tap_store):
        """Group scope routes to resolver.set_group_policy."""
        with patch("mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver") as mock_fn:
            mock_resolver = Mock()
            mock_fn.return_value = mock_resolver

            command = SetToolAccessPolicyCommand(scope="group", target_id="team-a", allow_list=[], deny_list=["bad"])
            handler.handle(command)

        mock_resolver.set_group_policy.assert_called_once()
        args = mock_resolver.set_group_policy.call_args[0]
        assert args[0] == "team-a"

    def test_member_scope_splits_target_id(self, handler, tap_store):
        """Member scope splits 'group_id:member_id' and calls set_member_policy."""
        with patch("mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver") as mock_fn:
            mock_resolver = Mock()
            mock_fn.return_value = mock_resolver

            command = SetToolAccessPolicyCommand(
                scope="member",
                target_id="group1:user42",
                allow_list=["safe"],
                deny_list=[],
            )
            handler.handle(command)

        mock_resolver.set_member_policy.assert_called_once()
        args = mock_resolver.set_member_policy.call_args[0]
        assert args[0] == "group1"
        assert args[1] == "user42"

    def test_emits_tool_access_policy_set_event(self, handler, tap_store, event_bus):
        """handle() publishes ToolAccessPolicySet event."""
        from mcp_hangar.domain.events import ToolAccessPolicySet

        with patch("mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver"):
            command = SetToolAccessPolicyCommand(scope="provider", target_id="math", allow_list=["add"], deny_list=[])
            handler.handle(command)

        event_bus.publish.assert_called_once()
        event = event_bus.publish.call_args[0][0]
        assert isinstance(event, ToolAccessPolicySet)
        assert event.scope == "provider"
        assert event.target_id == "math"


# ---------------------------------------------------------------------------
# ClearToolAccessPolicyHandler
# ---------------------------------------------------------------------------


class TestClearToolAccessPolicyHandler:
    """Tests for ClearToolAccessPolicyHandler."""

    @pytest.fixture
    def tap_store(self):
        return Mock()

    @pytest.fixture
    def event_bus(self):
        return Mock()

    @pytest.fixture
    def handler(self, tap_store, event_bus):
        return ClearToolAccessPolicyHandler(tap_store, event_bus)

    def test_returns_confirmation(self, handler):
        """handle() returns cleared=True confirmation dict."""
        command = ClearToolAccessPolicyCommand(scope="provider", target_id="math")
        result = handler.handle(command)

        assert result["cleared"] is True
        assert result["scope"] == "provider"
        assert result["target_id"] == "math"

    def test_calls_tap_store_clear_policy(self, handler, tap_store):
        """handle() removes policy from tap_store."""
        command = ClearToolAccessPolicyCommand(scope="group", target_id="team-b")
        handler.handle(command)

        tap_store.clear_policy.assert_called_once_with(scope="group", target_id="team-b")

    def test_emits_tool_access_policy_cleared_event(self, handler, event_bus):
        """handle() publishes ToolAccessPolicyCleared event."""
        from mcp_hangar.domain.events import ToolAccessPolicyCleared

        command = ClearToolAccessPolicyCommand(scope="provider", target_id="math")
        handler.handle(command)

        event_bus.publish.assert_called_once()
        event = event_bus.publish.call_args[0][0]
        assert isinstance(event, ToolAccessPolicyCleared)
        assert event.scope == "provider"
        assert event.target_id == "math"


# ---------------------------------------------------------------------------
# GetToolAccessPolicyHandler
# ---------------------------------------------------------------------------


class TestGetToolAccessPolicyHandler:
    """Tests for GetToolAccessPolicyHandler."""

    @pytest.fixture
    def tap_store(self):
        return Mock()

    @pytest.fixture
    def handler(self, tap_store):
        return GetToolAccessPolicyHandler(tap_store)

    def test_returns_found_false_when_absent(self, handler, tap_store):
        """handle() returns found=False when no policy exists."""
        tap_store.get_policy.return_value = None

        result = handler.handle(GetToolAccessPolicyQuery(scope="provider", target_id="math"))

        assert result["found"] is False
        assert result["scope"] == "provider"
        assert result["target_id"] == "math"
        assert result["allow_list"] == []
        assert result["deny_list"] == []

    def test_returns_found_true_with_policy(self, handler, tap_store):
        """handle() returns found=True with allow/deny lists when policy exists."""
        tap_store.get_policy.return_value = ToolAccessPolicy(
            allow_list=("add", "sub"),
            deny_list=("rm",),
        )

        result = handler.handle(GetToolAccessPolicyQuery(scope="provider", target_id="math"))

        assert result["found"] is True
        assert "add" in result["allow_list"]
        assert "sub" in result["allow_list"]
        assert "rm" in result["deny_list"]


# ---------------------------------------------------------------------------
# ListAllRolesHandler
# ---------------------------------------------------------------------------


class TestListAllRolesHandler:
    """Tests for ListAllRolesHandler."""

    @pytest.fixture
    def store(self):
        return InMemoryRoleStore()

    @pytest.fixture
    def handler(self, store):
        return ListAllRolesHandler(store)

    def test_include_builtin_true_returns_builtin_roles(self, handler):
        """include_builtin=True includes built-in roles in result."""
        result = handler.handle(ListAllRolesQuery(include_builtin=True))

        assert result["builtin_count"] > 0
        role_names = {r["name"] for r in result["roles"]}
        assert "admin" in role_names

    def test_include_builtin_false_returns_custom_only(self, store, handler):
        """include_builtin=False returns only custom roles."""
        from mcp_hangar.domain.value_objects import Role

        store.add_role(Role(name="custom-test", description="x", permissions=frozenset()))
        result = handler.handle(ListAllRolesQuery(include_builtin=False))

        role_names = {r["name"] for r in result["roles"]}
        assert "admin" not in role_names
        assert "custom-test" in role_names

    def test_result_structure(self, handler):
        """Result dict has expected keys."""
        result = handler.handle(ListAllRolesQuery())
        assert "roles" in result
        assert "total" in result
        assert "builtin_count" in result
        assert "custom_count" in result


# ---------------------------------------------------------------------------
# ListPrincipalsHandler
# ---------------------------------------------------------------------------


class TestListPrincipalsHandler:
    """Tests for ListPrincipalsHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryRoleStore()
        store.assign_role("user-1", "developer")
        store.assign_role("user-2", "viewer")
        return store

    @pytest.fixture
    def handler(self, store):
        return ListPrincipalsHandler(store)

    def test_returns_principals_with_roles(self, handler):
        """handle() returns principals that have role assignments."""
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] >= 2
        principal_ids = {p["principal_id"] for p in result["principals"]}
        assert "user-1" in principal_ids
        assert "user-2" in principal_ids

    def test_empty_when_no_assignments(self):
        """Returns empty list when no principals have roles."""
        handler = ListPrincipalsHandler(InMemoryRoleStore())
        result = handler.handle(ListPrincipalsQuery())
        assert result["principals"] == []
        assert result["total"] == 0
