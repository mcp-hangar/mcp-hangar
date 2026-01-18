"""Real-time operation feedback with progress events.

This module provides streaming progress updates for long-running
operations, giving users visibility into:

- Cold starts and provider launches
- Container initialization
- Tool discovery
- Network calls and execution

Usage with callback::

    from mcp_hangar import ProgressTracker, ProgressStage

    def on_progress(stage: str, message: str, elapsed_ms: float):
        print(f"⏳ [{stage}] {message} ({elapsed_ms:.0f}ms)")

    tracker = ProgressTracker(callback=on_progress)
    tracker.report(ProgressStage.LAUNCHING, "Starting...")
    tracker.complete(result)

See docs/guides/UX_IMPROVEMENTS.md for more examples.
"""

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .logging_config import get_logger

logger = get_logger(__name__)


class ProgressStage(str, Enum):
    """Stages of operation progress."""

    # Pre-execution stages
    COLD_START = "cold_start"
    LAUNCHING = "launching"
    INITIALIZING = "initializing"
    DISCOVERING_TOOLS = "discovering_tools"
    CONNECTING = "connecting"

    # Execution stages
    READY = "ready"
    EXECUTING = "executing"
    PROCESSING = "processing"

    # Completion stages
    COMPLETE = "complete"
    FAILED = "failed"
    RETRYING = "retrying"


class EventType(str, Enum):
    """Types of events in the stream."""

    PROGRESS = "progress"
    RESULT = "result"
    ERROR = "error"


@dataclass
class ProgressEvent:
    """A single progress update event.

    Attributes:
        type: Event type (progress, result, error)
        stage: Current operation stage
        message: Human-readable description
        elapsed_ms: Time since operation started
        details: Additional context data
        timestamp: Event timestamp
    """

    type: EventType = EventType.PROGRESS
    stage: ProgressStage = ProgressStage.EXECUTING
    message: str = ""
    elapsed_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    # For result/error events
    data: Any = None
    exception: Optional[Exception] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "type": self.type.value,
            "stage": self.stage.value,
            "message": self.message,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "timestamp": self.timestamp,
        }
        if self.details:
            result["details"] = self.details
        if self.data is not None:
            result["data"] = self.data
        if self.exception is not None:
            result["error"] = str(self.exception)
        return result


# Type alias for progress callback
ProgressCallback = Callable[[str, str, float], None]


class ProgressTracker:
    """Tracks and reports progress for a single operation.

    Can be used with callbacks or as a streaming iterator.

    Usage with callback:
        tracker = ProgressTracker(callback=my_callback)
        tracker.report(ProgressStage.COLD_START, "Launching container...")
        tracker.report(ProgressStage.READY, "Provider ready")
        tracker.complete(result)

    Usage as iterator:
        tracker = ProgressTracker()
        # In one thread/task:
        tracker.report(ProgressStage.LAUNCHING, "Starting...")
        tracker.complete(result)
        # In another:
        for event in tracker:
            logger.info("progress_event", event=event.to_dict())
    """

    def __init__(
        self,
        callback: Optional[ProgressCallback] = None,
        provider: str = "",
        operation: str = "",
    ):
        """Initialize progress tracker.

        Args:
            callback: Optional callback for progress updates
            provider: Provider name for context
            operation: Operation name for context
        """
        self._callback = callback
        self._provider = provider
        self._operation = operation
        self._start_time = time.time()
        self._events: list[ProgressEvent] = []
        self._completed = False
        self._result: Any = None
        self._error: Optional[Exception] = None

        # For iterator support
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._sync_queue: list[ProgressEvent] = []
        self._lock = threading.Lock()
        self._done = threading.Event()

    @property
    def elapsed_ms(self) -> float:
        """Time elapsed since operation started."""
        return (time.time() - self._start_time) * 1000

    def report(
        self,
        stage: ProgressStage,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Report a progress update.

        Args:
            stage: Current progress stage
            message: Human-readable description
            details: Additional context
        """
        elapsed = self.elapsed_ms
        event = ProgressEvent(
            type=EventType.PROGRESS,
            stage=stage,
            message=message,
            elapsed_ms=elapsed,
            details=details or {},
        )

        with self._lock:
            self._events.append(event)
            self._sync_queue.append(event)

        # Try to put in async queue (non-blocking)
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

        # Invoke callback if set
        if self._callback:
            try:
                self._callback(stage.value, message, elapsed)
            except Exception as e:
                logger.debug("progress_callback_error", error=str(e))

        logger.debug(
            "progress_reported",
            provider=self._provider,
            operation=self._operation,
            stage=stage.value,
            message=message,
            elapsed_ms=elapsed,
        )

    def complete(self, result: Any) -> None:
        """Mark operation as complete with result.

        Args:
            result: The operation result
        """
        elapsed = self.elapsed_ms
        event = ProgressEvent(
            type=EventType.RESULT,
            stage=ProgressStage.COMPLETE,
            message=f"Operation completed (total: {elapsed:.0f}ms)",
            elapsed_ms=elapsed,
            data=result,
        )

        with self._lock:
            self._events.append(event)
            self._sync_queue.append(event)
            self._completed = True
            self._result = result

        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

        self._done.set()

        if self._callback:
            try:
                self._callback(ProgressStage.COMPLETE.value, event.message, elapsed)
            except Exception:
                pass

        logger.debug(
            "progress_complete",
            provider=self._provider,
            operation=self._operation,
            elapsed_ms=elapsed,
        )

    def fail(self, error: Exception) -> None:
        """Mark operation as failed with error.

        Args:
            error: The exception that caused failure
        """
        elapsed = self.elapsed_ms
        event = ProgressEvent(
            type=EventType.ERROR,
            stage=ProgressStage.FAILED,
            message=f"Operation failed: {str(error)[:100]}",
            elapsed_ms=elapsed,
            exception=error,
        )

        with self._lock:
            self._events.append(event)
            self._sync_queue.append(event)
            self._completed = True
            self._error = error

        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

        self._done.set()

        if self._callback:
            try:
                self._callback(ProgressStage.FAILED.value, event.message, elapsed)
            except (TypeError, ValueError, RuntimeError) as e:
                logger.debug("progress_callback_error", stage="failed", error=str(e))

        logger.debug(
            "progress_failed",
            provider=self._provider,
            operation=self._operation,
            error=str(error)[:200],
            elapsed_ms=elapsed,
        )

    def __iter__(self) -> Iterator[ProgressEvent]:
        """Iterate over events synchronously."""
        while True:
            with self._lock:
                if self._sync_queue:
                    event = self._sync_queue.pop(0)
                    yield event
                    if event.type in (EventType.RESULT, EventType.ERROR):
                        return
                elif self._completed:
                    return

            # Wait a bit before checking again
            if not self._done.wait(timeout=0.01):
                continue

    async def __aiter__(self) -> AsyncIterator[ProgressEvent]:
        """Iterate over events asynchronously."""
        while True:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=0.1,
                )
                yield event
                if event.type in (EventType.RESULT, EventType.ERROR):
                    return
            except asyncio.TimeoutError:
                if self._completed:
                    return

    def get_all_events(self) -> list[ProgressEvent]:
        """Get all recorded events."""
        with self._lock:
            return list(self._events)


# =============================================================================
# Progress-Aware Operation Wrapper
# =============================================================================


class ProgressOperation:
    """Context manager for progress-tracked operations.

    Usage:
        async with ProgressOperation("math", "add", callback=my_cb) as progress:
            progress.report(ProgressStage.LAUNCHING, "Starting provider...")
            result = await do_work()
            progress.complete(result)
    """

    def __init__(
        self,
        provider: str,
        operation: str,
        callback: Optional[ProgressCallback] = None,
    ):
        self.provider = provider
        self.operation = operation
        self.tracker = ProgressTracker(
            callback=callback,
            provider=provider,
            operation=operation,
        )

    def __enter__(self) -> ProgressTracker:
        return self.tracker

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None and not self.tracker._completed:
            self.tracker.fail(exc_val)
        return False

    async def __aenter__(self) -> ProgressTracker:
        return self.tracker

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None and not self.tracker._completed:
            self.tracker.fail(exc_val)
        return False


# =============================================================================
# Standard Progress Messages
# =============================================================================

# Pre-defined messages for common operations
PROGRESS_MESSAGES = {
    ProgressStage.COLD_START: "Provider is cold, launching...",
    ProgressStage.LAUNCHING: "Starting {mode} provider...",
    ProgressStage.INITIALIZING: "Container started (PID: {pid}), initializing...",
    ProgressStage.DISCOVERING_TOOLS: "Discovering available tools...",
    ProgressStage.CONNECTING: "Connecting to provider...",
    ProgressStage.READY: "Provider ready, executing request...",
    ProgressStage.EXECUTING: "Calling tool '{tool}'...",
    ProgressStage.PROCESSING: "Processing response...",
    ProgressStage.COMPLETE: "Operation completed (total: {elapsed_ms:.0f}ms)",
    ProgressStage.FAILED: "Operation failed: {error}",
    ProgressStage.RETRYING: "Retrying (attempt {attempt}/{max_attempts})...",
}


def get_stage_message(
    stage: ProgressStage,
    **kwargs,
) -> str:
    """Get formatted message for a progress stage.

    Args:
        stage: The progress stage
        **kwargs: Format arguments for message template

    Returns:
        Formatted progress message
    """
    template = PROGRESS_MESSAGES.get(stage, stage.value)
    try:
        return template.format(**kwargs)
    except KeyError:
        return template


# =============================================================================
# Event Bus Integration
# =============================================================================


class ProgressEventHandler:
    """Event handler that forwards domain events to progress tracker.

    This bridges the gap between domain events and progress reporting,
    allowing existing code to emit progress updates without modification.
    """

    # Map domain event types to progress stages
    EVENT_STAGE_MAP = {
        "ProviderStarted": ProgressStage.READY,
        "ProviderStopped": ProgressStage.COMPLETE,
        "ProviderStateChanged": ProgressStage.INITIALIZING,
        "ToolInvocationRequested": ProgressStage.EXECUTING,
        "ToolInvocationCompleted": ProgressStage.COMPLETE,
        "ToolInvocationFailed": ProgressStage.FAILED,
    }

    def __init__(self):
        self._trackers: dict[str, ProgressTracker] = {}
        self._lock = threading.Lock()

    def register_tracker(self, correlation_id: str, tracker: ProgressTracker) -> None:
        """Register a tracker for a correlation ID."""
        with self._lock:
            self._trackers[correlation_id] = tracker

    def unregister_tracker(self, correlation_id: str) -> None:
        """Unregister a tracker."""
        with self._lock:
            self._trackers.pop(correlation_id, None)

    def handle(self, event: Any) -> None:
        """Handle a domain event and forward to appropriate tracker."""
        event_type = type(event).__name__
        stage = self.EVENT_STAGE_MAP.get(event_type)

        if not stage:
            return

        # Try to find correlation ID
        correlation_id = getattr(event, "correlation_id", None)
        if not correlation_id:
            return

        with self._lock:
            tracker = self._trackers.get(correlation_id)

        if tracker:
            message = self._format_event_message(event, event_type)
            tracker.report(stage, message, self._extract_details(event))

    def _format_event_message(self, event: Any, event_type: str) -> str:
        """Format event into progress message."""
        if event_type == "ProviderStarted":
            return f"Provider started ({getattr(event, 'tools_count', 0)} tools)"
        elif event_type == "ProviderStateChanged":
            new_state = getattr(event, "new_state", "unknown")
            return f"Provider state: {new_state}"
        elif event_type == "ToolInvocationRequested":
            tool = getattr(event, "tool_name", "unknown")
            return f"Invoking tool: {tool}"
        elif event_type == "ToolInvocationCompleted":
            duration = getattr(event, "duration_ms", 0)
            return f"Tool completed ({duration:.0f}ms)"
        elif event_type == "ToolInvocationFailed":
            error = getattr(event, "error_message", "unknown error")
            return f"Tool failed: {error[:50]}"
        return event_type

    def _extract_details(self, event: Any) -> dict[str, Any]:
        """Extract relevant details from event."""
        details = {}
        for attr in ["provider_id", "tool_name", "duration_ms", "error_type"]:
            if hasattr(event, attr):
                details[attr] = getattr(event, attr)
        return details


# Global progress event handler instance
_progress_handler: Optional[ProgressEventHandler] = None


def get_progress_handler() -> ProgressEventHandler:
    """Get or create the global progress event handler."""
    global _progress_handler
    if _progress_handler is None:
        _progress_handler = ProgressEventHandler()
    return _progress_handler


def create_progress_tracker(
    provider: str = "",
    operation: str = "",
    callback: Optional[ProgressCallback] = None,
    correlation_id: Optional[str] = None,
) -> ProgressTracker:
    """Create a progress tracker with optional event bus integration.

    Args:
        provider: Provider name
        operation: Operation name
        callback: Optional progress callback
        correlation_id: Optional correlation ID for event bus integration

    Returns:
        New ProgressTracker instance
    """
    tracker = ProgressTracker(
        callback=callback,
        provider=provider,
        operation=operation,
    )

    if correlation_id:
        handler = get_progress_handler()
        handler.register_tracker(correlation_id, tracker)

    return tracker
