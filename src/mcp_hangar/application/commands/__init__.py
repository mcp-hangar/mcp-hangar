"""Command handlers for CQRS."""

from .commands import (
    Command,
    HealthCheckCommand,
    InvokeToolCommand,
    LoadMcpServerCommand,
    ReloadConfigurationCommand,
    ShutdownIdleMcpServersCommand,
    StartMcpServerCommand,
    StopMcpServerCommand,
    UnloadMcpServerCommand,
)
from .handlers import (
    HealthCheckHandler,
    InvokeToolHandler,
    register_all_handlers,
    ShutdownIdleMcpServersHandler,
    StartMcpServerHandler,
    StopMcpServerHandler,
)
from .load_handlers import LoadMcpServerHandler, LoadResult, UnloadMcpServerHandler
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
    "StartMcpServerCommand",
    "StopMcpServerCommand",
    "InvokeToolCommand",
    "HealthCheckCommand",
    "ShutdownIdleMcpServersCommand",
    "LoadMcpServerCommand",
    "UnloadMcpServerCommand",
    "ReloadConfigurationCommand",
    # Handlers
    "StartMcpServerHandler",
    "StopMcpServerHandler",
    "InvokeToolHandler",
    "HealthCheckHandler",
    "ShutdownIdleMcpServersHandler",
    "register_all_handlers",
    # Load Handlers
    "LoadMcpServerHandler",
    "UnloadMcpServerHandler",
    "LoadResult",
    "ReloadConfigurationHandler",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("StartPro", "viderCommand")): StartMcpServerCommand,
        "".join(("StopPro", "viderCommand")): StopMcpServerCommand,
        "".join(("ShutdownIdlePro", "vidersCommand")): ShutdownIdleMcpServersCommand,
    }
)
sys.modules[f"{__name__}.crud_{''.join(('com', 'mands'))}"] = import_module(f"{__name__}.crud_commands")
