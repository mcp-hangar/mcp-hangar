"""Authentication and Authorization command handlers.

Implements CQRS command handlers for auth operations.
All handlers emit domain events via the event bus.
"""

from typing import Any

from mcp_hangar.domain.contracts.authentication import IApiKeyStore
from mcp_hangar.domain.contracts.authorization import IRoleStore
from mcp_hangar.domain.events import (
    CustomRoleCreated,
    CustomRoleDeleted,
    CustomRoleUpdated,
    ToolAccessPolicyCleared,
    ToolAccessPolicySet,
)
from mcp_hangar.domain.value_objects import Permission, Role
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.domain.contracts.command import CommandHandler
from mcp_hangar.logging_config import get_logger
from enterprise.auth.commands.commands import (
    AssignRoleCommand,
    ClearToolAccessPolicyCommand,
    CreateApiKeyCommand,
    CreateCustomRoleCommand,
    DeleteCustomRoleCommand,
    ListApiKeysCommand,
    RevokeApiKeyCommand,
    RevokeRoleCommand,
    SetToolAccessPolicyCommand,
    UpdateCustomRoleCommand,
)

logger = get_logger(__name__)


# =============================================================================
# API Key Command Handlers
# =============================================================================


class CreateApiKeyHandler(CommandHandler):
    """Handler for CreateApiKeyCommand.

    Creates a new API key and returns the raw key value.
    Note: The raw key is only returned once - it cannot be retrieved later.
    """

    def __init__(self, api_key_store: IApiKeyStore):
        self._store = api_key_store

    def handle(self, command: CreateApiKeyCommand) -> dict[str, Any]:
        """Create a new API key.

        Returns:
            Dict with key_id (for management) and raw_key (for authentication).
            The raw_key is only shown once!
        """
        logger.info(
            "creating_api_key",
            principal_id=command.principal_id,
            name=command.name,
            created_by=command.created_by,
        )

        raw_key = self._store.create_key(
            principal_id=command.principal_id,
            name=command.name,
            expires_at=command.expires_at,
            groups=command.groups,
            tenant_id=command.tenant_id,
            created_by=command.created_by,
        )

        # Get the key_id from the list (the raw_key is not stored)
        keys = self._store.list_keys(command.principal_id)
        key_metadata = next((k for k in keys if k.name == command.name), None)

        return {
            "key_id": key_metadata.key_id if key_metadata else None,
            "raw_key": raw_key,
            "principal_id": command.principal_id,
            "name": command.name,
            "expires_at": command.expires_at.isoformat() if command.expires_at else None,
            "warning": "Save this key now - it cannot be retrieved later!",
        }


class RevokeApiKeyHandler(CommandHandler):
    """Handler for RevokeApiKeyCommand.

    Revokes an API key, making it unusable for authentication.
    """

    def __init__(self, api_key_store: IApiKeyStore):
        self._store = api_key_store

    def handle(self, command: RevokeApiKeyCommand) -> dict[str, Any]:
        """Revoke an API key.

        Returns:
            Dict with revocation status.
        """
        logger.info(
            "revoking_api_key",
            key_id=command.key_id,
            revoked_by=command.revoked_by,
            reason=command.reason,
        )

        success = self._store.revoke_key(
            key_id=command.key_id,
            revoked_by=command.revoked_by,
            reason=command.reason,
        )

        return {
            "key_id": command.key_id,
            "revoked": success,
            "revoked_by": command.revoked_by,
            "reason": command.reason,
        }


class ListApiKeysHandler(CommandHandler):
    """Handler for ListApiKeysCommand.

    Note: This is technically a query, but kept as command for simplicity.
    In a strict CQRS implementation, this would be a query handler.
    """

    def __init__(self, api_key_store: IApiKeyStore):
        self._store = api_key_store

    def handle(self, command: ListApiKeysCommand) -> dict[str, Any]:
        """List API keys for a principal.

        Returns:
            Dict with list of key metadata (not the actual keys).
        """
        keys = self._store.list_keys(command.principal_id)

        return {
            "principal_id": command.principal_id,
            "keys": [
                {
                    "key_id": k.key_id,
                    "name": k.name,
                    "created_at": k.created_at.isoformat() if k.created_at else None,
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "revoked": k.revoked,
                }
                for k in keys
            ],
            "count": len(keys),
        }


# =============================================================================
# Role Command Handlers
# =============================================================================


class AssignRoleHandler(CommandHandler):
    """Handler for AssignRoleCommand.

    Assigns a role to a principal with optional scope.
    """

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, command: AssignRoleCommand) -> dict[str, Any]:
        """Assign a role to a principal.

        Returns:
            Dict with assignment confirmation.
        """
        logger.info(
            "assigning_role",
            principal_id=command.principal_id,
            role_name=command.role_name,
            scope=command.scope,
            assigned_by=command.assigned_by,
        )

        self._store.assign_role(
            principal_id=command.principal_id,
            role_name=command.role_name,
            scope=command.scope,
            assigned_by=command.assigned_by,
        )

        return {
            "principal_id": command.principal_id,
            "role_name": command.role_name,
            "scope": command.scope,
            "assigned": True,
            "assigned_by": command.assigned_by,
        }


class RevokeRoleHandler(CommandHandler):
    """Handler for RevokeRoleCommand.

    Revokes a role from a principal.
    """

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, command: RevokeRoleCommand) -> dict[str, Any]:
        """Revoke a role from a principal.

        Returns:
            Dict with revocation confirmation.
        """
        logger.info(
            "revoking_role",
            principal_id=command.principal_id,
            role_name=command.role_name,
            scope=command.scope,
            revoked_by=command.revoked_by,
        )

        self._store.revoke_role(
            principal_id=command.principal_id,
            role_name=command.role_name,
            scope=command.scope,
            revoked_by=command.revoked_by,
        )

        return {
            "principal_id": command.principal_id,
            "role_name": command.role_name,
            "scope": command.scope,
            "revoked": True,
            "revoked_by": command.revoked_by,
        }


class CreateCustomRoleHandler(CommandHandler):
    """Handler for CreateCustomRoleCommand.

    Creates a custom role with specified permissions and emits CustomRoleCreated.
    """

    def __init__(self, role_store: IRoleStore, event_bus: Any = None):
        self._store = role_store
        self._event_bus = event_bus

    def handle(self, command: CreateCustomRoleCommand) -> dict[str, Any]:
        """Create a custom role.

        Returns:
            Dict with role creation confirmation.
        """
        logger.info(
            "creating_custom_role",
            role_name=command.role_name,
            permissions_count=len(command.permissions),
            created_by=command.created_by,
        )

        # Parse permission strings to Permission objects
        permissions = frozenset(Permission.parse(p) for p in command.permissions)

        role = Role(
            name=command.role_name,
            description=command.description,
            permissions=permissions,
        )

        self._store.add_role(role)

        if self._event_bus is not None:
            self._event_bus.publish(
                CustomRoleCreated(
                    role_name=command.role_name,
                    permissions=list(command.permissions),
                    description=command.description or None,
                )
            )

        return {
            "role_name": command.role_name,
            "description": command.description,
            "permissions_count": len(permissions),
            "created": True,
            "created_by": command.created_by,
        }


class DeleteCustomRoleHandler(CommandHandler):
    """Handler for DeleteCustomRoleCommand.

    Deletes a custom role from the store and emits CustomRoleDeleted.
    Raises CannotModifyBuiltinRoleError if the role is built-in.
    Raises RoleNotFoundError if the role does not exist.
    """

    def __init__(self, role_store: IRoleStore, event_bus: Any):
        self._store = role_store
        self._event_bus = event_bus

    def handle(self, command: DeleteCustomRoleCommand) -> dict[str, Any]:
        """Delete a custom role.

        Returns:
            Dict with deletion confirmation.
        """
        logger.info(
            "deleting_custom_role",
            role_name=command.role_name,
            deleted_by=command.deleted_by,
        )

        # Raises CannotModifyBuiltinRoleError or RoleNotFoundError if invalid
        self._store.delete_role(command.role_name)
        self._event_bus.publish(CustomRoleDeleted(role_name=command.role_name))

        return {
            "role_name": command.role_name,
            "deleted": True,
            "deleted_by": command.deleted_by,
        }


class UpdateCustomRoleHandler(CommandHandler):
    """Handler for UpdateCustomRoleCommand.

    Updates a custom role's permissions and description, emits CustomRoleUpdated.
    Raises CannotModifyBuiltinRoleError if the role is built-in.
    Raises RoleNotFoundError if the role does not exist.
    """

    def __init__(self, role_store: IRoleStore, event_bus: Any):
        self._store = role_store
        self._event_bus = event_bus

    def handle(self, command: UpdateCustomRoleCommand) -> dict[str, Any]:
        """Update a custom role.

        Returns:
            Dict with updated role info.
        """
        logger.info(
            "updating_custom_role",
            role_name=command.role_name,
            permissions_count=len(command.permissions),
            updated_by=command.updated_by,
        )

        permissions = [Permission.parse(p) for p in command.permissions]
        # Raises CannotModifyBuiltinRoleError or RoleNotFoundError if invalid
        updated_role = self._store.update_role(command.role_name, permissions, command.description)

        self._event_bus.publish(
            CustomRoleUpdated(
                role_name=command.role_name,
                permissions=list(command.permissions),
                description=command.description,
            )
        )

        return {
            "role_name": updated_role.name,
            "description": updated_role.description,
            "permissions_count": len(updated_role.permissions),
            "updated": True,
            "updated_by": command.updated_by,
        }


class SetToolAccessPolicyHandler(CommandHandler):
    """Handler for SetToolAccessPolicyCommand.

    Persists the policy to the TAP store, updates the in-memory resolver,
    and emits ToolAccessPolicySet.
    """

    def __init__(self, tap_store: Any, event_bus: Any):
        self._tap_store = tap_store
        self._event_bus = event_bus

    def handle(self, command: SetToolAccessPolicyCommand) -> dict[str, Any]:
        """Set a tool access policy.

        Returns:
            Dict with confirmation.
        """
        logger.info(
            "setting_tool_access_policy",
            scope=command.scope,
            target_id=command.target_id,
            allow_count=len(command.allow_list),
            deny_count=len(command.deny_list),
        )

        # 1. Persist to store
        self._tap_store.set_policy(
            scope=command.scope,
            target_id=command.target_id,
            allow_list=command.allow_list,
            deny_list=command.deny_list,
        )

        # 2. Update in-memory resolver so runtime enforcement is immediate
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        policy = ToolAccessPolicy(
            allow_list=tuple(command.allow_list),
            deny_list=tuple(command.deny_list),
        )
        if command.scope == "provider":
            resolver.set_mcp_server_policy(command.target_id, policy)
        elif command.scope == "group":
            resolver.set_group_policy(command.target_id, policy)
        elif command.scope == "member":
            # target_id for member scope is "group_id:member_id"
            parts = command.target_id.split(":", 1)
            if len(parts) == 2:
                resolver.set_member_policy(parts[0], parts[1], policy)
            else:
                resolver.set_member_policy(command.target_id, command.target_id, policy)

        # 3. Emit domain event
        self._event_bus.publish(
            ToolAccessPolicySet(
                scope=command.scope,
                target_id=command.target_id,
                allow_list=command.allow_list,
                deny_list=command.deny_list,
            )
        )

        return {
            "scope": command.scope,
            "target_id": command.target_id,
            "allow_list": command.allow_list,
            "deny_list": command.deny_list,
            "set": True,
        }


class ClearToolAccessPolicyHandler(CommandHandler):
    """Handler for ClearToolAccessPolicyCommand.

    Removes the policy from the TAP store and emits ToolAccessPolicyCleared.
    """

    def __init__(self, tap_store: Any, event_bus: Any):
        self._tap_store = tap_store
        self._event_bus = event_bus

    def handle(self, command: ClearToolAccessPolicyCommand) -> dict[str, Any]:
        """Clear a tool access policy.

        Returns:
            Dict with confirmation.
        """
        logger.info(
            "clearing_tool_access_policy",
            scope=command.scope,
            target_id=command.target_id,
        )

        self._tap_store.clear_policy(scope=command.scope, target_id=command.target_id)
        self._event_bus.publish(ToolAccessPolicyCleared(scope=command.scope, target_id=command.target_id))

        return {
            "scope": command.scope,
            "target_id": command.target_id,
            "cleared": True,
        }


def register_auth_command_handlers(
    command_bus,
    api_key_store: IApiKeyStore | None = None,
    role_store: IRoleStore | None = None,
    tap_store: Any = None,
    event_bus: Any = None,
) -> None:
    """Register all auth command handlers with the command bus.

    Args:
        command_bus: CommandBus instance.
        api_key_store: API key store (optional, handlers skipped if None).
        role_store: Role store (optional, handlers skipped if None).
        tap_store: Tool access policy store (optional, TAP handlers skipped if None).
        event_bus: Event bus for emitting domain events (optional).
    """
    if api_key_store:
        command_bus.register(CreateApiKeyCommand, CreateApiKeyHandler(api_key_store))
        command_bus.register(RevokeApiKeyCommand, RevokeApiKeyHandler(api_key_store))
        command_bus.register(ListApiKeysCommand, ListApiKeysHandler(api_key_store))
        logger.info("auth_api_key_handlers_registered")

    if role_store:
        command_bus.register(AssignRoleCommand, AssignRoleHandler(role_store))
        command_bus.register(RevokeRoleCommand, RevokeRoleHandler(role_store))
        command_bus.register(CreateCustomRoleCommand, CreateCustomRoleHandler(role_store, event_bus=event_bus))
        command_bus.register(DeleteCustomRoleCommand, DeleteCustomRoleHandler(role_store, event_bus=event_bus))
        command_bus.register(UpdateCustomRoleCommand, UpdateCustomRoleHandler(role_store, event_bus=event_bus))
        logger.info("auth_role_handlers_registered")

    if tap_store:
        command_bus.register(SetToolAccessPolicyCommand, SetToolAccessPolicyHandler(tap_store, event_bus=event_bus))
        command_bus.register(ClearToolAccessPolicyCommand, ClearToolAccessPolicyHandler(tap_store, event_bus=event_bus))
        logger.info("auth_tap_handlers_registered")
