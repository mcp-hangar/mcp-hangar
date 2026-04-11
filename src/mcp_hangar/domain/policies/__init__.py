"""Domain policies for MCP Hangar.

Policies encapsulate domain rules and classification logic that can be
applied across different contexts without coupling to specific aggregates.
"""

from .provider_health import (
    classify_provider_health,
    classify_provider_health_from_provider,
    ProviderHealthClassification,
    to_health_status_string,
)

__all__ = [
    "ProviderHealthClassification",
    "classify_provider_health",
    "classify_provider_health_from_provider",
    "to_health_status_string",
]
