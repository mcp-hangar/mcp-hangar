"""Command handlers for CQRS."""

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

# Auth commands and handlers live in enterprise/auth/commands/.
# Re-export conditionally for backwards compatibility.
try:
    from enterprise.auth.commands.commands import (  # noqa: F401
        AssignRoleCommand,
        CreateApiKeyCommand,
        CreateCustomRoleCommand,
        ListApiKeysCommand,
        RevokeApiKeyCommand,
        RevokeRoleCommand,
    )
    from enterprise.auth.commands.handlers import (  # noqa: F401
        AssignRoleHandler,
        CreateApiKeyHandler,
        CreateCustomRoleHandler,
        ListApiKeysHandler,
        register_auth_command_handlers,
        RevokeApiKeyHandler,
        RevokeRoleHandler,
    )
except ImportError:
    pass

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
]
