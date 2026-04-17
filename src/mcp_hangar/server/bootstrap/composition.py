"""Shared server composition state and runtime accessors.

This module centralizes bootstrap-owned mutable state that is shared across
server startup and runtime wiring.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING, cast
import warnings

from ...application.discovery import DiscoveryOrchestrator
from ...application.sagas import GroupRebalanceSaga
from ...bootstrap.runtime import create_runtime
from ...domain.model import ProviderGroup
from ...infrastructure.runtime_store import RuntimeProviderStore

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

# Runtime wiring
_runtime: Runtime | None = None
_runtime_lock = Lock()

_DEPRECATED_RUNTIME_EXPORTS: dict[str, tuple[str, str]] = {
    "PROVIDER_REPOSITORY": ("repository", "get_runtime().repository"),
    "EVENT_BUS": ("event_bus", "get_runtime().event_bus"),
    "COMMAND_BUS": ("command_bus", "get_runtime().command_bus"),
    "QUERY_BUS": ("query_bus", "get_runtime().query_bus"),
    "RATE_LIMIT_CONFIG": ("rate_limit_config", "get_runtime().rate_limit_config"),
    "RATE_LIMITER": ("rate_limiter", "get_runtime().rate_limiter"),
    "INPUT_VALIDATOR": ("input_validator", "get_runtime().input_validator"),
    "SECURITY_HANDLER": ("security_handler", "get_runtime().security_handler"),
    "PROVIDERS": ("repository", "get_runtime().repository"),
}

if TYPE_CHECKING:
    COMMAND_BUS: object
    EVENT_BUS: object
    INPUT_VALIDATOR: object
    PROVIDER_REPOSITORY: object
    PROVIDERS: object
    QUERY_BUS: object
    RATE_LIMIT_CONFIG: object
    RATE_LIMITER: object
    SECURITY_HANDLER: object

# Provider Groups storage
GROUPS: dict[str, ProviderGroup] = {}

# Runtime (hot-loaded) providers storage
RUNTIME_PROVIDERS: RuntimeProviderStore = RuntimeProviderStore()

# Saga and discovery instances (initialized in main())
_group_rebalance_saga: GroupRebalanceSaga | None = None
_discovery_orchestrator: DiscoveryOrchestrator | None = None


def get_runtime() -> Runtime:
    """Get the lazily initialized runtime singleton."""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = create_runtime()
    return _runtime


def __getattr__(name: str) -> object:
    """Lazily resolve deprecated runtime-backed module attributes."""
    export = _DEPRECATED_RUNTIME_EXPORTS.get(name)
    if export is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    runtime_attr, replacement = export
    warnings.warn(
        f"mcp_hangar.server.bootstrap.composition.{name} is deprecated; use {replacement} instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return cast(object, getattr(get_runtime(), runtime_attr))


def set_discovery_orchestrator(orchestrator: DiscoveryOrchestrator | None) -> None:
    """Set the discovery orchestrator instance."""
    global _discovery_orchestrator
    _discovery_orchestrator = orchestrator


def get_discovery_orchestrator() -> DiscoveryOrchestrator | None:
    """Get the discovery orchestrator instance."""
    return _discovery_orchestrator


def set_group_rebalance_saga(saga: GroupRebalanceSaga | None) -> None:
    """Set the group rebalance saga instance."""
    global _group_rebalance_saga
    _group_rebalance_saga = saga


def get_group_rebalance_saga() -> GroupRebalanceSaga | None:
    """Get the group rebalance saga instance."""
    return _group_rebalance_saga


def get_runtime_providers() -> RuntimeProviderStore:
    """Get the runtime providers store."""
    return RUNTIME_PROVIDERS


__all__ = [
    "COMMAND_BUS",
    "EVENT_BUS",
    "GROUPS",
    "INPUT_VALIDATOR",
    "PROVIDER_REPOSITORY",
    "PROVIDERS",
    "QUERY_BUS",
    "RATE_LIMIT_CONFIG",
    "RATE_LIMITER",
    "RUNTIME_PROVIDERS",
    "SECURITY_HANDLER",
    "get_discovery_orchestrator",
    "get_group_rebalance_saga",
    "get_runtime",
    "get_runtime_providers",
    "set_discovery_orchestrator",
    "set_group_rebalance_saga",
]
