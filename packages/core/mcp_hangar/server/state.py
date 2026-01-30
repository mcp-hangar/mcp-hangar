"""Server state management - BACKWARD COMPATIBILITY MODULE.

This module provides backward compatibility with code that relies on
global state variables. New code should use ApplicationContext from
context.py instead.

DEPRECATED: Direct use of PROVIDERS, COMMAND_BUS, etc. is deprecated.
Use get_context() from context.py for dependency injection.

Example migration:
    # Old (deprecated):
    from ..state import COMMAND_BUS, PROVIDERS
    COMMAND_BUS.send(command)

    # New (recommended):
    from ..context import get_context
    ctx = get_context()
    ctx.command_bus.send(command)
"""

from typing import TYPE_CHECKING

from ..application.discovery import DiscoveryOrchestrator
from ..application.sagas import GroupRebalanceSaga
from ..bootstrap.runtime import create_runtime
from ..domain.model import ProviderGroup
from ..infrastructure.runtime_store import RuntimeProviderStore
from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..domain.repository import IProviderRepository

logger = get_logger(__name__)


class ProviderDict:
    """Dictionary-like wrapper around provider repository for backward compatibility."""

    def __init__(self, repository: "IProviderRepository"):
        self._repo = repository

    def __getitem__(self, key: str):
        provider = self._repo.get(key)
        if provider is None:
            raise KeyError(key)
        return provider

    def __setitem__(self, key: str, value):
        self._repo.add(key, value)

    def __contains__(self, key: str) -> bool:
        return self._repo.exists(key)

    def __len__(self) -> int:
        return self._repo.count()

    def get(self, key: str, default=None):
        return self._repo.get(key) or default

    def items(self):
        return self._repo.get_all().items()

    def keys(self):
        return self._repo.get_all_ids()

    def values(self):
        return self._repo.get_all().values()


# Runtime wiring
_RUNTIME = create_runtime()

# Convenience bindings
PROVIDER_REPOSITORY = _RUNTIME.repository
EVENT_BUS = _RUNTIME.event_bus
COMMAND_BUS = _RUNTIME.command_bus
QUERY_BUS = _RUNTIME.query_bus
RATE_LIMIT_CONFIG = _RUNTIME.rate_limit_config
RATE_LIMITER = _RUNTIME.rate_limiter
INPUT_VALIDATOR = _RUNTIME.input_validator
SECURITY_HANDLER = _RUNTIME.security_handler

# Provider dict backed by repository
PROVIDERS = ProviderDict(PROVIDER_REPOSITORY)

# Provider Groups storage
GROUPS: dict[str, ProviderGroup] = {}

# Runtime (hot-loaded) providers storage
RUNTIME_PROVIDERS: RuntimeProviderStore = RuntimeProviderStore()

# Saga and discovery instances (initialized in main())
_GROUP_REBALANCE_SAGA: GroupRebalanceSaga | None = None
_DISCOVERY_ORCHESTRATOR: DiscoveryOrchestrator | None = None


def get_runtime():
    """Get the runtime instance."""
    return _RUNTIME


def set_discovery_orchestrator(orchestrator: DiscoveryOrchestrator | None) -> None:
    """Set the discovery orchestrator instance."""
    global _DISCOVERY_ORCHESTRATOR
    _DISCOVERY_ORCHESTRATOR = orchestrator


def get_discovery_orchestrator() -> DiscoveryOrchestrator | None:
    """Get the discovery orchestrator instance."""
    return _DISCOVERY_ORCHESTRATOR


def set_group_rebalance_saga(saga: GroupRebalanceSaga | None) -> None:
    """Set the group rebalance saga instance."""
    global _GROUP_REBALANCE_SAGA
    _GROUP_REBALANCE_SAGA = saga


def get_group_rebalance_saga() -> GroupRebalanceSaga | None:
    """Get the group rebalance saga instance."""
    return _GROUP_REBALANCE_SAGA


def get_runtime_providers() -> RuntimeProviderStore:
    """Get the runtime providers store."""
    return RUNTIME_PROVIDERS
