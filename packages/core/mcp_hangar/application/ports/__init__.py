"""Application ports - interfaces for external dependencies."""

from .observability import NullObservabilityAdapter, ObservabilityPort, SpanHandle

__all__ = [
    "ObservabilityPort",
    "SpanHandle",
    "NullObservabilityAdapter",
]
