"""Domain contracts - interfaces for external dependencies.

This module defines contracts (abstract interfaces) that the domain layer
depends on. Implementations are provided by the infrastructure layer.
"""

from .metrics_publisher import IMetricsPublisher
from .provider_runtime import ProviderRuntime

__all__ = [
    "IMetricsPublisher",
    "ProviderRuntime",
]

