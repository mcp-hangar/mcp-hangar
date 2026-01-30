"""Application commands - represent user intentions.

Commands are immutable data structures that represent actions to be performed.
They are named in imperative form (StartProvider, not ProviderStarted).
"""

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Command(ABC):
    """Base class for all commands.

    Commands are immutable and represent a request to perform an action.
    They should be named in imperative form (StartProvider, not ProviderStarted).
    """

    pass


@dataclass(frozen=True)
class StartProviderCommand(Command):
    """Command to start a provider."""

    provider_id: str


@dataclass(frozen=True)
class StopProviderCommand(Command):
    """Command to stop a provider."""

    provider_id: str
    reason: str = "user_request"


@dataclass(frozen=True)
class InvokeToolCommand(Command):
    """Command to invoke a tool on a provider."""

    provider_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0


@dataclass(frozen=True)
class HealthCheckCommand(Command):
    """Command to perform health check on a provider."""

    provider_id: str


@dataclass(frozen=True)
class ShutdownIdleProvidersCommand(Command):
    """Command to shutdown all idle providers."""

    pass


@dataclass(frozen=True)
class LoadProviderCommand(Command):
    """Command to load a provider from the registry at runtime."""

    name: str
    force_unverified: bool = False
    user_id: str | None = None


@dataclass(frozen=True)
class UnloadProviderCommand(Command):
    """Command to unload a hot-loaded provider."""

    provider_id: str
    user_id: str | None = None
