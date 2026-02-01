"""Data models for batch invocations.

Contains data classes for batch call specifications and results,
plus configuration constants.
"""

from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Configuration Constants
# =============================================================================

DEFAULT_MAX_CONCURRENCY = 10
MAX_CONCURRENCY_LIMIT = 20
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
    provider: str
    tool: str
    arguments: dict[str, Any]
    timeout: float | None = None
    max_retries: int = 1  # Default: no retries (single attempt)


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
