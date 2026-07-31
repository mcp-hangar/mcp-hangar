"""Data models for batch invocations.

Contains data classes for batch call specifications and results,
plus configuration constants.
"""

from dataclasses import dataclass, field
from typing import Any

from ....application.tasks.tool_pin_context import CurrentToolPin
from ....domain.value_objects.identity import IdentityContext

# =============================================================================
# Configuration Constants
# =============================================================================

# Per-batch concurrency (what the LLM/caller requests via hangar_call parameter)
DEFAULT_MAX_CONCURRENCY = 10
MAX_CONCURRENCY_LIMIT = 50  # Upper bound for per-batch max_concurrency parameter

# System-wide concurrency limits (configured via config.yaml, shared across batches)
DEFAULT_GLOBAL_CONCURRENCY = 50
"""Default global limit across all mcp_servers and batches (0 = unlimited)."""

DEFAULT_PROVIDER_CONCURRENCY = 10
"""Default per-mcp_server concurrency limit (0 = unlimited)."""

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 300.0
MAX_CALLS_PER_BATCH = 100
MAX_RESPONSE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB per call
MAX_TOTAL_RESPONSE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB total

DEFAULT_MAX_RETRIES = 3
"""Default number of retry attempts per call."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CallSpec:
    """Specification for a single call within a batch."""

    index: int
    call_id: str
    mcp_server: str
    tool: str
    arguments: dict[str, Any]
    timeout: float | None = None
    max_retries: int = 1  # Default: no retries (single attempt)
    metadata: dict[str, str] | None = None  # W3C TraceContext headers (traceparent, tracestate)


@dataclass
class RetryMetadata:
    """Metadata about retry attempts for a call."""

    attempts: int
    retries: list[str]  # List of error types from retries
    total_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for response."""
        return {
            "attempts": self.attempts,
            "retries": self.retries,
            "total_time_ms": round(self.total_time_ms, 2),
        }


@dataclass
class RelayCapture:
    """Context captured in a batch WORKER when an upstream ``tools/call`` returns
    a task handle, to be governed on the MAIN LOOP (ADR-014 D4).

    The ThreadPoolExecutor worker only DETECTS the upstream task result and
    snapshots the request-scoped context needed to govern it; the actual
    ``store.relay_and_govern(...)`` (register + ``TaskCreated`` emit) runs on the
    main loop at the P3.3 seam, BEFORE the handle reaches the client. Governance
    therefore binds on the request path, never in a worker thread.

    Attributes:
        identity: The worker's :class:`IdentityContext` snapshot (or ``None`` when
            unattributed). Re-bound at the seam so the store's owner cross-check
            sees the same tenant the worker authorized.
        pin: The worker's :class:`CurrentToolPin` snapshot (or ``None`` when the
            call was not digest-pinned). Re-bound at the seam so the governed
            entry is pinned to the digest authorized at invoke time (#320).
        target_server_id: Resolved backend (standalone server or selected group
            member) the task lives on; first half of the composite ledger key.
        correlation_id: Request correlation id (the call's ``call_id``).
        upstream: The raw upstream task-handle dict (``result.result``); handed
            back to the client verbatim once governed.
        logical_mcp_server: Logical mcp_server (or group) id the call targeted.
        tool: Tool name invoked on the call.
    """

    identity: IdentityContext | None
    pin: CurrentToolPin | None
    target_server_id: str
    correlation_id: str
    upstream: dict[str, Any]
    logical_mcp_server: str
    tool: str


@dataclass
class CallResult:
    """Result of a single call within a batch."""

    index: int
    call_id: str
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False
    truncated_reason: str | None = None
    original_size_bytes: int | None = None
    retry_metadata: RetryMetadata | None = None
    continuation_id: str | None = None  # For fetching full response when truncated
    # ADR-014 P3: set by a batch worker when the upstream returned a task handle
    # and the relay kill-switch is on. Carries the captured request context so the
    # MAIN-LOOP seam (hangar_call) can govern the relay; None on every other path.
    relay_capture: "RelayCapture | None" = None


@dataclass
class BatchResult:
    """Result of a batch invocation."""

    batch_id: str
    success: bool
    total: int
    succeeded: int
    failed: int
    elapsed_ms: float
    results: list[CallResult] = field(default_factory=list)
    cancelled: int = 0


@dataclass
class ValidationError:
    """Validation error for a single call."""

    index: int
    field: str
    message: str
