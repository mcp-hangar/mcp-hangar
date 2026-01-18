"""CLI commands for authentication management.

Provides commands for API key management and role assignment.

Usage:
    # Create an API key
    mcp-hangar auth create-key --principal user:admin --name "Admin Key" --role admin

    # List keys for a principal
    mcp-hangar auth list-keys --principal user:admin

    # Revoke a key
    mcp-hangar auth revoke-key KEY_ID

    # Assign a role
    mcp-hangar auth assign-role --principal user:dev --role developer
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from ..domain.security.roles import list_builtin_roles
from ..infrastructure.auth.api_key_authenticator import InMemoryApiKeyStore
from ..infrastructure.auth.rbac_authorizer import InMemoryRoleStore


def create_auth_parser(subparsers) -> argparse.ArgumentParser:
    """Create the auth subparser with all auth commands.

    Args:
        subparsers: The subparsers object from the main parser.

    Returns:
        The auth parser.
    """
    auth_parser = subparsers.add_parser(
        "auth",
        help="Authentication management commands",
        description="Commands for managing API keys and role assignments.",
    )

    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", help="Auth commands")

    # create-key command
    create_key_parser = auth_subparsers.add_parser(
        "create-key",
        help="Create a new API key",
        description="Create a new API key for a principal. The key is only shown once!",
    )
    create_key_parser.add_argument(
        "--principal",
        required=True,
        help="Principal ID for the key (e.g., 'user:admin', 'service:ci-pipeline')",
    )
    create_key_parser.add_argument(
        "--name",
        required=True,
        help="Human-readable name for the key",
    )
    create_key_parser.add_argument(
        "--role",
        action="append",
        default=[],
        help="Roles to assign (can be repeated)",
    )
    create_key_parser.add_argument(
        "--expires",
        type=int,
        help="Expiration in days",
    )
    create_key_parser.add_argument(
        "--tenant",
        help="Tenant ID for multi-tenancy",
    )

    # list-keys command
    list_keys_parser = auth_subparsers.add_parser(
        "list-keys",
        help="List API keys for a principal",
    )
    list_keys_parser.add_argument(
        "--principal",
        required=True,
        help="Principal ID",
    )

    # revoke-key command
    revoke_key_parser = auth_subparsers.add_parser(
        "revoke-key",
        help="Revoke an API key",
    )
    revoke_key_parser.add_argument(
        "key_id",
        help="Key ID to revoke",
    )
    revoke_key_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # assign-role command
    assign_role_parser = auth_subparsers.add_parser(
        "assign-role",
        help="Assign a role to a principal",
    )
    assign_role_parser.add_argument(
        "--principal",
        required=True,
        help="Principal ID",
    )
    assign_role_parser.add_argument(
        "--role",
        required=True,
        help="Role name",
    )
    assign_role_parser.add_argument(
        "--scope",
        default="global",
        help="Scope (global, tenant:X, namespace:Y)",
    )

    # revoke-role command
    revoke_role_parser = auth_subparsers.add_parser(
        "revoke-role",
        help="Revoke a role from a principal",
    )
    revoke_role_parser.add_argument(
        "--principal",
        required=True,
        help="Principal ID",
    )
    revoke_role_parser.add_argument(
        "--role",
        required=True,
        help="Role name",
    )
    revoke_role_parser.add_argument(
        "--scope",
        default="global",
        help="Scope",
    )

    # list-roles command
    _list_roles_parser = auth_subparsers.add_parser(  # noqa: F841 - used by argparse
        "list-roles",
        help="List available built-in roles",
    )

    return auth_parser


def handle_auth_command(args, key_store: InMemoryApiKeyStore, role_store: InMemoryRoleStore) -> int:
    """Handle auth CLI commands.

    Args:
        args: Parsed arguments.
        key_store: API key store.
        role_store: Role store.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    if args.auth_command == "create-key":
        return _handle_create_key(args, key_store, role_store)
    elif args.auth_command == "list-keys":
        return _handle_list_keys(args, key_store)
    elif args.auth_command == "revoke-key":
        return _handle_revoke_key(args, key_store)
    elif args.auth_command == "assign-role":
        return _handle_assign_role(args, role_store)
    elif args.auth_command == "revoke-role":
        return _handle_revoke_role(args, role_store)
    elif args.auth_command == "list-roles":
        return _handle_list_roles()
    else:
        print(f"Unknown auth command: {args.auth_command}", file=sys.stderr)
        return 1


def _handle_create_key(args, key_store: InMemoryApiKeyStore, role_store: InMemoryRoleStore) -> int:
    """Handle create-key command."""
    principal_id = args.principal
    name = args.name
    roles = args.role or []
    expires_days = args.expires
    tenant_id = args.tenant

    # Calculate expiration
    expires_at: datetime | None = None
    if expires_days:
        expires_at = datetime.now(UTC) + timedelta(days=expires_days)

    # Validate roles exist
    for role_name in roles:
        if role_store.get_role(role_name) is None:
            print(f"Error: Unknown role '{role_name}'", file=sys.stderr)
            print(f"Available roles: {', '.join(list_builtin_roles())}", file=sys.stderr)
            return 1

    # Create the key
    raw_key = key_store.create_key(
        principal_id=principal_id,
        name=name,
        expires_at=expires_at,
        tenant_id=tenant_id,
    )

    # Assign roles
    for role_name in roles:
        role_store.assign_role(principal_id, role_name)

    # Output
    print(f"API Key created for {principal_id}")
    print(f"Key: {raw_key}")
    print()
    print("⚠️  Save this key now - it cannot be retrieved later!")
    print()

    if expires_at:
        print(f"Expires: {expires_at.isoformat()}")

    if roles:
        print(f"Roles assigned: {', '.join(roles)}")

    return 0


def _handle_list_keys(args, key_store: InMemoryApiKeyStore) -> int:
    """Handle list-keys command."""
    principal_id = args.principal
    keys = key_store.list_keys(principal_id)

    if not keys:
        print(f"No keys found for {principal_id}")
        return 0

    print(f"API Keys for {principal_id}:")
    print()

    for key in keys:
        status = "🔴 REVOKED" if key.revoked else "🟢 ACTIVE"
        if not key.revoked and key.is_expired:
            status = "🟡 EXPIRED"

        print(f"{status} {key.key_id}: {key.name}")
        print(f"   Created: {key.created_at.isoformat()}")
        if key.expires_at:
            print(f"   Expires: {key.expires_at.isoformat()}")
        if key.last_used_at:
            print(f"   Last used: {key.last_used_at.isoformat()}")
        print()

    return 0


def _handle_revoke_key(args, key_store: InMemoryApiKeyStore) -> int:
    """Handle revoke-key command."""
    key_id = args.key_id

    # Check if key exists
    key_metadata = key_store.get_key_by_id(key_id)
    if key_metadata is None:
        print(f"Error: Key {key_id} not found", file=sys.stderr)
        return 1

    if key_metadata.revoked:
        print(f"Key {key_id} is already revoked")
        return 0

    # Confirm unless --yes flag
    if not args.yes:
        confirm = input(f"Are you sure you want to revoke key {key_id}? [y/N] ")
        if confirm.lower() not in ("y", "yes"):
            print("Cancelled")
            return 0

    # Revoke
    if key_store.revoke_key(key_id):
        print(f"Key {key_id} revoked")
        return 0
    else:
        print(f"Error: Failed to revoke key {key_id}", file=sys.stderr)
        return 1


def _handle_assign_role(args, role_store: InMemoryRoleStore) -> int:
    """Handle assign-role command."""
    principal_id = args.principal
    role_name = args.role
    scope = args.scope

    # Validate role exists
    if role_store.get_role(role_name) is None:
        print(f"Error: Unknown role '{role_name}'", file=sys.stderr)
        print(f"Available roles: {', '.join(list_builtin_roles())}", file=sys.stderr)
        return 1

    try:
        role_store.assign_role(principal_id, role_name, scope)
        print(f"Assigned role '{role_name}' to {principal_id} (scope: {scope})")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _handle_revoke_role(args, role_store: InMemoryRoleStore) -> int:
    """Handle revoke-role command."""
    principal_id = args.principal
    role_name = args.role
    scope = args.scope

    role_store.revoke_role(principal_id, role_name, scope)
    print(f"Revoked role '{role_name}' from {principal_id} (scope: {scope})")
    return 0


def _handle_list_roles() -> int:
    """Handle list-roles command."""
    from ..domain.security.roles import BUILTIN_ROLES

    print("Available built-in roles:")
    print()

    for name, role in BUILTIN_ROLES.items():
        print(f"  {name}")
        print(f"    {role.description}")
        print(f"    Permissions: {len(role.permissions)}")
        print()

    return 0
