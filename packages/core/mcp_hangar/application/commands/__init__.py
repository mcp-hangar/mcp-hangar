"""Command handlers for CQRS."""

from .auth_commands import (
    AssignRoleCommand,
    CreateApiKeyCommand,
    CreateCustomRoleCommand,
    ListApiKeysCommand,
    RevokeApiKeyCommand,
    RevokeRoleCommand,
)
from .auth_handlers import (
    AssignRoleHandler,
    CreateApiKeyHandler,
    CreateCustomRoleHandler,
    ListApiKeysHandler,
    RevokeApiKeyHandler,
    RevokeRoleHandler,
    register_auth_command_handlers,
)
from .commands import (
    Command,
    HealthCheckCommand,
    InvokeToolCommand,
    ShutdownIdleProvidersCommand,
    StartProviderCommand,
    StopProviderCommand,
)
from .handlers import (
    HealthCheckHandler,
    InvokeToolHandler,
    ShutdownIdleProvidersHandler,
    StartProviderHandler,
    StopProviderHandler,
    register_all_handlers,
)

__all__ = [
    # Commands
    "Command",
    "StartProviderCommand",
    "StopProviderCommand",
    "InvokeToolCommand",
    "HealthCheckCommand",
    "ShutdownIdleProvidersCommand",
    # Auth Commands
    "CreateApiKeyCommand",
    "RevokeApiKeyCommand",
    "ListApiKeysCommand",
    "AssignRoleCommand",
    "RevokeRoleCommand",
    "CreateCustomRoleCommand",
    # Handlers
    "StartProviderHandler",
    "StopProviderHandler",
    "InvokeToolHandler",
    "HealthCheckHandler",
    "ShutdownIdleProvidersHandler",
    "register_all_handlers",
    # Auth Handlers
    "CreateApiKeyHandler",
    "RevokeApiKeyHandler",
    "ListApiKeysHandler",
    "AssignRoleHandler",
    "RevokeRoleHandler",
    "CreateCustomRoleHandler",
    "register_auth_command_handlers",
]
