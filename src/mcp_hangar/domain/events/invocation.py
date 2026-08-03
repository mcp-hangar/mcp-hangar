# pyright: reportExplicitAny=false

"""Tool-invocation events."""

from dataclasses import dataclass, field
from typing import Any

from ..value_objects.compat import accepts_legacy_provider_id
from .base import DomainEvent


# Tool Invocation Events


@accepts_legacy_provider_id
@dataclass
class ToolInvocationRequested(DomainEvent):
    """Published when a tool invocation is requested."""

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    identity_context: dict[str, Any] | None = None

    def __post_init__(self):
        # The hand-written constructor this replaces did `arguments or {}`, so an
        # explicit `arguments=None` produced an empty dict rather than None. Some
        # callers pass the value through from an optional, and every consumer
        # indexes it without a None check.
        if self.arguments is None:
            self.arguments = {}
        super().__init__()


@accepts_legacy_provider_id
@dataclass
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    duration_ms: float = 0.0
    result_size_bytes: int = 0
    identity_context: dict[str, Any] | None = None

    def __post_init__(self):
        super().__init__()


@accepts_legacy_provider_id
@dataclass
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    mcp_server_id: str
    tool_name: str = ""
    correlation_id: str = ""
    duration_ms: float = 0.0
    error_message: str = ""
    error_type: str = ""
    identity_context: dict[str, Any] | None = None

    def __post_init__(self):
        super().__init__()
