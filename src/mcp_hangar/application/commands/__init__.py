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

_ENTERPRISE_AUTH_COMMANDS = {
    "AssignRoleCommand",
    "CreateApiKeyCommand",
    "CreateCustomRoleCommand",
    "ListApiKeysCommand",
    "RevokeApiKeyCommand",
    "RevokeRoleCommand",
    "AssignRoleHandler",
    "CreateApiKeyHandler",
    "CreateCustomRoleHandler",
    "ListApiKeysHandler",
    "RevokeApiKeyHandler",
    "RevokeRoleHandler",
    "register_auth_command_handlers",
}


def __getattr__(name: str):  # noqa: ANN001
    if name in _ENTERPRISE_AUTH_COMMANDS:
        try:
            if "Handler" in name or name.startswith("register"):
                mod_name = "mcp_hangar.auth.commands.handlers"
            else:
                mod_name = "mcp_hangar.auth.commands.commands"
            return getattr(import_module(mod_name), name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (enterprise not installed)") from err
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
