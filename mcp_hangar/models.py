"""Core data models for the MCP registry with explicit state management.

This module provides backward compatibility imports for legacy code.
New code should import directly from the domain layer.

Deprecated imports (use domain layer instead):
- ProviderState -> from mcp_hangar.domain.value_objects import ProviderState
- MCPError, ProviderStartError, etc. -> from mcp_hangar.domain.exceptions import ...
"""

from dataclasses import dataclass, field
import threading
from typing import Any, Dict, Literal

# Re-export all exceptions from the canonical location for backward compatibility
from .domain.exceptions import (
    ClientError,
    MCPError,
    ProviderDegradedError,
    ProviderError,
    ProviderNotFoundError,
    ProviderNotReadyError,
    ProviderStartError,
    ToolError,
    ToolInvocationError,
    ToolNotFoundError,
    ValidationError,
)

# Re-export ProviderState from the canonical location
from .domain.value_objects import ProviderState


@dataclass
class ProviderSpec:
    """Specification for how to start and manage a provider.

    Deprecated: Use Provider aggregate directly or ProviderConfig value object.
    """

    provider_id: str
    mode: Literal["subprocess", "docker", "remote"]

    # subprocess mode
    command: list[str] | None = None

    # docker mode
    image: str | None = None

    # remote mode
    endpoint: str | None = None

    # common configuration
    env: Dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300
    health_check_interval_s: int = 60
    max_consecutive_failures: int = 3


@dataclass
class ToolSchema:
    """Schema for a tool provided by a provider.

    Deprecated: Use mcp_hangar.domain.model.tool_catalog.ToolSchema instead.
    """

    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any] | None = None


@dataclass
class InvocationContext:
    """Context for a tool invocation, used for tracing and diagnostics.

    Deprecated: Use domain value objects directly for new code.
    """

    correlation_id: str
    provider_id: str
    tool_name: str
    started_at: float
    timeout_s: float


@dataclass
class ProviderHealth:
    """Health metrics for a provider.

    Deprecated: Use mcp_hangar.domain.model.health_tracker.HealthTracker instead.
    """

    consecutive_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    total_invocations: int = 0
    total_failures: int = 0


@dataclass
class ProviderConnection:
    """Complete state for a provider connection.

    Deprecated: Use Provider aggregate which manages its own state.

    All mutable fields must be accessed under the lock.
    """

    spec: ProviderSpec
    state: ProviderState = ProviderState.COLD
    client: Any | None = None
    tools: Dict[str, ToolSchema] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    health: ProviderHealth = field(default_factory=ProviderHealth)
    last_used: float = 0.0
    lock: threading.RLock = field(default_factory=threading.RLock)


# Note: These models are kept for backward compatibility with:
# - tests/test_integration.py
# - tests/test_provider_manager.py
# - tests/test_real_provider.py
# - tests/test_repository.py
# - tests/test_stress_performance.py
#
# Migration path:
# 1. New code should use Provider aggregate from mcp_hangar.domain.model
# 2. Legacy tests will be migrated in a separate refactoring
# 3. This module will be deprecated once migration is complete
