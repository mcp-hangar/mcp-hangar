"""Application commands - represent user intentions.

Commands are immutable data structures that represent actions to be performed.
They are named in imperative form (StartMcpServer, not McpServerStarted).
"""

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


from ...domain.value_objects.compat import resolve_legacy_mcp_server_id as _resolve_legacy_mcp_server_id


@dataclass(frozen=True)
class Command(ABC):
    """Base class for all commands.

    Commands are immutable and represent a request to perform an action.
    They should be named in imperative form (StartMcpServer, not McpServerStarted).
    """

    pass


@dataclass(frozen=True, init=False)
class StartMcpServerCommand(Command):
    """Command to start a mcp_server."""

    mcp_server_id: str

    def __init__(self, mcp_server_id: str | None = None, **kwargs: object):
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        import warnings

        warnings.warn(
            "Command.provider_id is deprecated; use mcp_server_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.mcp_server_id


@dataclass(frozen=True, init=False)
class StopMcpServerCommand(Command):
    """Command to stop a mcp_server."""

    mcp_server_id: str
    reason: str = "user_request"

    def __init__(self, mcp_server_id: str | None = None, reason: str = "user_request", **kwargs: object):
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        object.__setattr__(self, "reason", reason)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        import warnings

        warnings.warn(
            "Command.provider_id is deprecated; use mcp_server_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.mcp_server_id


@dataclass(frozen=True, init=False)
class InvokeToolCommand(Command):
    """Command to invoke a tool on a mcp_server."""

    mcp_server_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    # A granted approval id from the gate (#921). Consumed by the aggregate's
    # L7 requireApproval branch, which otherwise fails closed; carries no
    # authority of its own -- the gate granted and revalidated it upstream.
    l7_approval_id: str | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
        timeout: float = 30.0,
        l7_approval_id: str | None = None,
        **kwargs: object,
    ):
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "arguments", arguments or {})
        object.__setattr__(self, "timeout", timeout)
        object.__setattr__(self, "l7_approval_id", l7_approval_id)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        import warnings

        warnings.warn(
            "Command.provider_id is deprecated; use mcp_server_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.mcp_server_id


@dataclass(frozen=True, init=False)
class HealthCheckCommand(Command):
    """Command to perform health check on a mcp_server."""

    mcp_server_id: str

    def __init__(self, mcp_server_id: str | None = None, **kwargs: object):
        object.__setattr__(self, "mcp_server_id", _resolve_legacy_mcp_server_id(mcp_server_id, kwargs))
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

    @property
    def provider_id(self) -> str:
        import warnings

        warnings.warn(
            "Command.provider_id is deprecated; use mcp_server_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.mcp_server_id


@dataclass(frozen=True)
class ShutdownIdleMcpServersCommand(Command):
    """Command to shutdown all idle mcp_servers."""

    pass


@dataclass(frozen=True)
class LoadMcpServerCommand(Command):
    """Command to load a mcp_server from the registry at runtime."""

    name: str
    force_unverified: bool = False
    user_id: str | None = None
    # Tool access filtering for hot-loaded mcp_servers. The three lists are the
    # three outcomes `ToolAccessPolicy` has; carrying only two is what made a
    # runtime-loaded server unable to gate a tool at all (#685).
    allow_tools: list[str] | None = None
    deny_tools: list[str] | None = None
    approval_tools: list[str] | None = None


@dataclass(frozen=True)
class UnloadMcpServerCommand(Command):
    """Command to unload a hot-loaded mcp_server."""

    mcp_server_id: str
    user_id: str | None = None


@dataclass(frozen=True)
class ReloadConfigurationCommand(Command):
    """Command to reload configuration from file."""

    config_path: str | None = None
    graceful: bool = True
    requested_by: str = "manual"


# Legacy aliases for renamed commands -- public API back-compat.
# Remove together with the deprecated `provider_id` kwarg (planned 2026-Q3).
StartProviderCommand = StartMcpServerCommand
StopProviderCommand = StopMcpServerCommand
ShutdownIdleProvidersCommand = ShutdownIdleMcpServersCommand
LoadProviderCommand = LoadMcpServerCommand
UnloadProviderCommand = UnloadMcpServerCommand
