# pyright: reportExplicitAny=false

"""Tool-invocation events."""

from dataclasses import dataclass, field
from typing import Any

from ..value_objects.compat import (
    resolve_legacy_mcp_server_id as _resolve_legacy_mcp_server_id,
)
from .base import DomainEvent


# Tool Invocation Events


@dataclass(init=False)
class ToolInvocationRequested(DomainEvent):
    """Published when a tool invocation is requested."""

    mcp_server_id: str
    tool_name: str
    correlation_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    identity_context: dict[str, Any] | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        correlation_id: str = "",
        arguments: dict[str, Any] | None = None,
        identity_context: dict[str, Any] | None = None,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.correlation_id = correlation_id
        self.arguments = arguments or {}
        self.identity_context = identity_context
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    mcp_server_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    result_size_bytes: int
    identity_context: dict[str, Any] | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        correlation_id: str = "",
        duration_ms: float = 0.0,
        result_size_bytes: int = 0,
        identity_context: dict[str, Any] | None = None,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.correlation_id = correlation_id
        self.duration_ms = duration_ms
        self.result_size_bytes = result_size_bytes
        self.identity_context = identity_context
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    mcp_server_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    error_message: str
    error_type: str
    identity_context: dict[str, Any] | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        correlation_id: str = "",
        duration_ms: float = 0.0,
        error_message: str = "",
        error_type: str = "",
        identity_context: dict[str, Any] | None = None,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.correlation_id = correlation_id
        self.duration_ms = duration_ms
        self.error_message = error_message
        self.error_type = error_type
        self.identity_context = identity_context
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()
