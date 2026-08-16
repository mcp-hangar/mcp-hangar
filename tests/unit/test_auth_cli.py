"""`mcp-hangar auth ...`: argument parsing and every subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, Mock


class TestAuthCLICreateParser:
    """Test create_auth_parser builds correct argument structure."""

    def test_parser_has_auth_subcommands(self):
        from mcp_hangar.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        # Parse each subcommand to ensure they exist
        args = auth_parser.parse_args(["create-key", "--principal", "user:a", "--name", "key1"])
        assert args.principal == "user:a"
        assert args.name == "key1"

    def test_list_keys_subcommand(self):
        from mcp_hangar.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["list-keys", "--principal", "user:b"])
        assert args.principal == "user:b"

    def test_revoke_key_subcommand_with_yes(self):
        from mcp_hangar.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["revoke-key", "KEY123", "--yes"])
        assert args.key_id == "KEY123"
        assert args.yes is True

    def test_assign_role_subcommand_defaults(self):
        from mcp_hangar.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["assign-role", "--principal", "user:c", "--role", "admin"])
        assert args.scope == "global"

    def test_revoke_role_subcommand(self):
        from mcp_hangar.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(
            ["revoke-role", "--principal", "user:c", "--role", "admin", "--scope", "tenant:x"]
        )
        assert args.scope == "tenant:x"

    def test_create_key_with_roles_and_expires(self):
        from mcp_hangar.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(
            [
                "create-key",
                "--principal",
                "user:a",
                "--name",
                "key1",
                "--role",
                "admin",
                "--role",
                "dev",
                "--expires",
                "30",
                "--tenant",
                "acme",
            ]
        )
        assert args.role == ["admin", "dev"]
        assert args.expires == 30
        assert args.tenant == "acme"


class TestHandleAuthCommand:
    """Test handle_auth_command routing."""

    def test_routes_to_create_key(self, capsys):
        from mcp_hangar.auth.cli import handle_auth_command
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            auth_command="create-key",
            principal="user:admin",
            name="Test Key",
            role=[],
            expires=None,
            tenant=None,
        )

        result = handle_auth_command(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "API Key created" in output

    def test_routes_to_list_keys_no_keys(self, capsys):
        from mcp_hangar.auth.cli import handle_auth_command
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(auth_command="list-keys", principal="user:admin")
        result = handle_auth_command(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "No keys found" in output

    def test_routes_to_list_roles(self, capsys):
        from mcp_hangar.auth.cli import handle_auth_command
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(auth_command="list-roles")
        result = handle_auth_command(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Available built-in roles" in output

    def test_unknown_command_returns_1(self, capsys):
        from mcp_hangar.auth.cli import handle_auth_command
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(auth_command="unknown-cmd")
        result = handle_auth_command(args, key_store, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Unknown auth command" in stderr_output


class TestHandleCreateKey:
    """Test _handle_create_key details."""

    def test_create_key_with_expiration(self, capsys):
        from mcp_hangar.auth.cli import _handle_create_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Expiring Key",
            role=[],
            expires=30,
            tenant=None,
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Expires:" in output

    def test_create_key_with_invalid_role_fails(self, capsys):
        from mcp_hangar.auth.cli import _handle_create_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Key",
            role=["nonexistent_role"],
            expires=None,
            tenant=None,
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Unknown role" in stderr_output

    def test_create_key_with_valid_role_assigns_it(self, capsys):
        from mcp_hangar.auth.cli import _handle_create_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Key",
            role=["admin"],  # admin is a builtin role
            expires=None,
            tenant="acme",
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Roles assigned: admin" in output

    def test_create_key_with_tenant(self, capsys):
        from mcp_hangar.auth.cli import _handle_create_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Tenant Key",
            role=[],
            expires=None,
            tenant="acme",
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 0


class TestHandleListKeys:
    """Test _handle_list_keys."""

    def test_list_keys_with_active_key(self, capsys):
        from mcp_hangar.auth.cli import _handle_list_keys
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="My Key")

        args = argparse.Namespace(principal="user:admin")
        result = _handle_list_keys(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "ACTIVE" in output
        assert "My Key" in output

    def test_list_keys_with_revoked_key(self, capsys):
        from mcp_hangar.auth.cli import _handle_list_keys
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Revoked Key")
        keys = key_store.list_keys("user:admin")
        key_store.revoke_key(keys[0].key_id)

        args = argparse.Namespace(principal="user:admin")
        result = _handle_list_keys(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "REVOKED" in output


class TestHandleRevokeKey:
    """Test _handle_revoke_key."""

    def test_revoke_nonexistent_key(self, capsys):
        from mcp_hangar.auth.cli import _handle_revoke_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        args = argparse.Namespace(key_id="nonexistent", yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "not found" in stderr_output

    def test_revoke_already_revoked_key(self, capsys):
        from mcp_hangar.auth.cli import _handle_revoke_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id
        key_store.revoke_key(key_id)

        args = argparse.Namespace(key_id=key_id, yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "already revoked" in output

    def test_revoke_with_confirmation_yes(self, capsys, monkeypatch):
        from mcp_hangar.auth.cli import _handle_revoke_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        args = argparse.Namespace(key_id=key_id, yes=False)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "revoked" in output

    def test_revoke_with_confirmation_cancelled(self, capsys, monkeypatch):
        from mcp_hangar.auth.cli import _handle_revoke_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        args = argparse.Namespace(key_id=key_id, yes=False)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Cancelled" in output

    def test_revoke_with_yes_flag_skips_confirmation(self, capsys):
        from mcp_hangar.auth.cli import _handle_revoke_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        args = argparse.Namespace(key_id=key_id, yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "revoked" in output.lower()

    def test_revoke_failure(self, capsys):
        from mcp_hangar.auth.cli import _handle_revoke_key
        from mcp_hangar.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        # Mock revoke_key to return False
        key_store.revoke_key = Mock(return_value=False)

        args = argparse.Namespace(key_id=key_id, yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Failed to revoke" in stderr_output


class TestHandleAssignRole:
    """Test _handle_assign_role."""

    def test_assign_unknown_role_fails(self, capsys):
        from mcp_hangar.auth.cli import _handle_assign_role
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="nonexistent", scope="global")
        result = _handle_assign_role(args, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Unknown role" in stderr_output

    def test_assign_valid_role_succeeds(self, capsys):
        from mcp_hangar.auth.cli import _handle_assign_role
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="admin", scope="global")
        result = _handle_assign_role(args, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Assigned role" in output

    def test_assign_role_with_scope(self, capsys):
        from mcp_hangar.auth.cli import _handle_assign_role
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="developer", scope="tenant:x")
        result = _handle_assign_role(args, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "tenant:x" in output

    def test_assign_role_value_error_caught(self, capsys):
        from mcp_hangar.auth.cli import _handle_assign_role
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        role_store.assign_role = Mock(side_effect=ValueError("duplicate assignment"))
        # Need get_role to return something so we pass the unknown role check
        role_store.get_role = Mock(return_value=MagicMock())

        args = argparse.Namespace(principal="user:a", role="admin", scope="global")
        result = _handle_assign_role(args, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "duplicate assignment" in stderr_output


class TestHandleRevokeRole:
    """Test _handle_revoke_role."""

    def test_revoke_role_succeeds(self, capsys):
        from mcp_hangar.auth.cli import _handle_revoke_role
        from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="admin", scope="global")
        result = _handle_revoke_role(args, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Revoked role" in output


class TestHandleListRoles:
    """Test _handle_list_roles."""

    def test_list_roles_output(self, capsys):
        from mcp_hangar.auth.cli import _handle_list_roles

        result = _handle_list_roles()
        assert result == 0
        output = capsys.readouterr().out
        assert "Available built-in roles" in output
        # Should list at least admin role
        assert "admin" in output
