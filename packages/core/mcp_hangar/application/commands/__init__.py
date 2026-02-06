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
    register_auth_command_handlers,
    RevokeApiKeyHandler,
    RevokeRoleHandler,
)
from .commands import (
    Command,
    HealthCheckCommand,
    InvokeToolCommand,
    LoadProviderCommand,
    ReloadConfigurationCommand,
    ShutdownIdleProvidersCommand,
    StartProviderCommand,
    StopProviderCommand,
    UnloadProviderCommand,
)
from .handlers import (
    HealthCheckHandler,
    InvokeToolHandler,
    register_all_handlers,
    ShutdownIdleProvidersHandler,
    StartProviderHandler,
    StopProviderHandler,
)
from .load_handlers import LoadProviderHandler, LoadResult, UnloadProviderHandler
from .reload_handler import ReloadConfigurationHandler

__all__ = [
    # Commands
    "Command",
    "StartProviderCommand",
    "StopProviderCommand",
    "InvokeToolCommand",
    "HealthCheckCommand",
    "ShutdownIdleProvidersCommand",
    "LoadProviderCommand",
    "UnloadProviderCommand",
    "ReloadConfigurationCommand",
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
    # Load Handlers
    "LoadProviderHandler",
    "UnloadProviderHandler",
    "LoadResult",
    "ReloadConfigurationHandler",
    # Auth Handlers
    "CreateApiKeyHandler",
    "RevokeApiKeyHandler",
    "ListApiKeysHandler",
    "AssignRoleHandler",
    "RevokeRoleHandler",
    "CreateCustomRoleHandler",
    "register_auth_command_handlers",
]
