"""Application ports - interfaces for external dependencies."""

from .async_task import IAsyncTaskSubmitter
from .bus import ICommandBus, IQueryBus
from .config_loader import IConfigLoader
from .log_buffers import ILogBuffers
from .observability import NullObservabilityAdapter, ObservabilityPort, SpanHandle
from .saga import (
    EventTriggeredSaga,
    ISagaManager,
    Saga,
    SagaContext,
    SagaState,
    SagaStep,
)

__all__ = [
    # Observability
    "ObservabilityPort",
    "SpanHandle",
    "NullObservabilityAdapter",
    # Async task
    "IAsyncTaskSubmitter",
    # Bus protocols
    "ICommandBus",
    "IQueryBus",
    # Config loader
    "IConfigLoader",
    # Log buffers
    "ILogBuffers",
    # Saga abstractions
    "EventTriggeredSaga",
    "ISagaManager",
    "Saga",
    "SagaContext",
    "SagaState",
    "SagaStep",
]
