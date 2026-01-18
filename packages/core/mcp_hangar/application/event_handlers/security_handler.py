"""
Security event handler for the MCP Registry.

Provides dedicated security audit logging for:
- Authentication and authorization events
- Access control violations
- Rate limit violations
- Suspicious activity detection
- Input validation failures
- Command injection attempts
"""

import hashlib
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ...domain.events import (
    DomainEvent,
    HealthCheckFailed,
    ProviderDegraded,
    ProviderStarted,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from ...logging_config import get_logger

logger = get_logger(__name__)


class SecurityEventType(Enum):
    """Types of security events."""

    # Access events
    ACCESS_GRANTED = "access_granted"
    ACCESS_DENIED = "access_denied"

    # Rate limiting
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    RATE_LIMIT_WARNING = "rate_limit_warning"

    # Validation
    VALIDATION_FAILED = "validation_failed"
    INJECTION_ATTEMPT = "injection_attempt"

    # Provider security
    PROVIDER_START_BLOCKED = "provider_start_blocked"
    SUSPICIOUS_COMMAND = "suspicious_command"
    UNAUTHORIZED_TOOL = "unauthorized_tool"

    # Health and availability
    REPEATED_FAILURES = "repeated_failures"
    PROVIDER_COMPROMISE_SUSPECTED = "provider_compromise_suspected"

    # Configuration
    CONFIG_CHANGE = "config_change"
    SENSITIVE_DATA_ACCESS = "sensitive_data_access"


class SecuritySeverity(Enum):
    """Severity levels for security events."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityEvent:
    """Represents a security-related event."""

    event_type: SecurityEventType
    severity: SecuritySeverity
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Context
    provider_id: str | None = None
    tool_name: str | None = None
    source_ip: str | None = None
    user_id: str | None = None

    # Details
    details: dict[str, Any] = field(default_factory=dict)

    # Tracking
    event_id: str = field(default_factory=lambda: "")
    correlation_id: str | None = None

    def __post_init__(self):
        """Generate event ID if not provided."""
        if not self.event_id:
            # Generate deterministic ID from content
            content = f"{self.event_type.value}:{self.timestamp.isoformat()}:{self.message}"
            self.event_id = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "provider_id": self.provider_id,
            "tool_name": self.tool_name,
            "source_ip": self.source_ip,
            "user_id": self.user_id,
            "details": self.details,
            "correlation_id": self.correlation_id,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class SecurityEventSink(ABC):
    """Abstract interface for security event destinations."""

    @abstractmethod
    def emit(self, event: SecurityEvent) -> None:
        """Emit a security event."""
        pass


class LogSecuritySink(SecurityEventSink):
    """Security sink that writes to structured logs."""

    def __init__(self, logger_name: str = "security"):
        self._logger = logging.getLogger(logger_name)

    def emit(self, event: SecurityEvent) -> None:
        """Log the security event with appropriate level."""
        log_data = {"security_event": event.to_dict()}

        # Map severity to log level
        if event.severity == SecuritySeverity.CRITICAL:
            self._logger.critical(json.dumps(log_data))
        elif event.severity == SecuritySeverity.HIGH:
            self._logger.error(json.dumps(log_data))
        elif event.severity == SecuritySeverity.MEDIUM:
            self._logger.warning(json.dumps(log_data))
        elif event.severity == SecuritySeverity.LOW:
            self._logger.info(json.dumps(log_data))
        else:
            self._logger.debug(json.dumps(log_data))


class InMemorySecuritySink(SecurityEventSink):
    """In-memory security sink for testing and recent event queries."""

    def __init__(self, max_events: int = 10000):
        self._events: list[SecurityEvent] = []
        self._max_events = max_events
        self._lock = threading.Lock()

    def emit(self, event: SecurityEvent) -> None:
        """Store the security event."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]

    def query(
        self,
        event_type: SecurityEventType | None = None,
        severity: SecuritySeverity | None = None,
        provider_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[SecurityEvent]:
        """Query stored security events."""
        with self._lock:
            results = []
            for event in reversed(self._events):
                if len(results) >= limit:
                    break

                if event_type and event.event_type != event_type:
                    continue
                if severity and event.severity != severity:
                    continue
                if provider_id and event.provider_id != provider_id:
                    continue
                if since and event.timestamp < since:
                    continue

                results.append(event)

            return results

    def get_severity_counts(self) -> dict[str, int]:
        """Get counts by severity level."""
        with self._lock:
            counts = {s.value: 0 for s in SecuritySeverity}
            for event in self._events:
                counts[event.severity.value] += 1
            return counts

    def clear(self) -> None:
        """Clear all stored events."""
        with self._lock:
            self._events.clear()

    @property
    def count(self) -> int:
        """Get total event count."""
        with self._lock:
            return len(self._events)


class CallbackSecuritySink(SecurityEventSink):
    """Security sink that calls a callback function."""

    def __init__(self, callback):
        self._callback = callback

    def emit(self, event: SecurityEvent) -> None:
        """Call the callback with the event."""
        try:
            self._callback(event)
        except Exception as e:
            logger.error(f"Security callback failed: {e}")


class CompositeSecuritySink(SecurityEventSink):
    """Security sink that emits to multiple sinks."""

    def __init__(self, sinks: list[SecurityEventSink]):
        self._sinks = sinks

    def emit(self, event: SecurityEvent) -> None:
        """Emit to all configured sinks."""
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception as e:
                logger.error(f"Security sink {type(sink).__name__} failed: {e}")

    def add_sink(self, sink: SecurityEventSink) -> None:
        """Add a sink."""
        self._sinks.append(sink)

    def remove_sink(self, sink: SecurityEventSink) -> None:
        """Remove a sink."""
        if sink in self._sinks:
            self._sinks.remove(sink)


class SecurityEventHandler:
    """
    Handler for domain events that detects and logs security-relevant activity.

    Monitors for:
    - Repeated failures (potential attacks)
    - Unusual patterns
    - Rate limit violations
    - Validation failures
    """

    # Thresholds for anomaly detection
    FAILURE_THRESHOLD = 5  # Failures before warning
    CRITICAL_FAILURE_THRESHOLD = 10  # Failures before critical alert
    TIME_WINDOW_S = 300  # 5 minute window for tracking

    def __init__(
        self,
        sink: SecurityEventSink | None = None,
        enable_anomaly_detection: bool = True,
    ):
        """
        Initialize the security handler.

        Args:
            sink: Where to emit security events (defaults to log sink)
            enable_anomaly_detection: Whether to detect anomalies in event patterns
        """
        self._sink = sink or LogSecuritySink()
        self._enable_anomaly_detection = enable_anomaly_detection

        # Tracking for anomaly detection
        self._failure_counts: dict[str, list[float]] = {}  # provider_id -> timestamps
        self._lock = threading.Lock()

    def handle(self, event: DomainEvent) -> None:
        """
        Handle a domain event, checking for security implications.

        Args:
            event: The domain event to process
        """
        # Dispatch to specific handlers
        handlers = {
            ProviderStarted: self._handle_provider_started,
            ProviderStopped: self._handle_provider_stopped,
            ProviderDegraded: self._handle_provider_degraded,
            ToolInvocationCompleted: self._handle_tool_invocation_completed,
            ToolInvocationFailed: self._handle_tool_invocation_failed,
            HealthCheckFailed: self._handle_health_check_failed,
        }

        handler = handlers.get(type(event))
        if handler:
            handler(event)

        # Run anomaly detection
        if self._enable_anomaly_detection:
            self._check_anomalies(event)

    def _handle_provider_started(self, event: ProviderStarted) -> None:
        """Handle provider start event."""
        # Log provider starts for audit trail
        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.ACCESS_GRANTED,
                severity=SecuritySeverity.INFO,
                message=f"Provider started: {event.provider_id}",
                provider_id=event.provider_id,
                details={
                    "mode": event.mode,
                    "tools_count": event.tools_count,
                    "startup_duration_ms": event.startup_duration_ms,
                },
                correlation_id=event.event_id,
            )
        )

    def _handle_provider_stopped(self, event: ProviderStopped) -> None:
        """Handle provider stop event."""
        # Clear failure tracking for this provider
        with self._lock:
            self._failure_counts.pop(event.provider_id, None)

    def _handle_provider_degraded(self, event: ProviderDegraded) -> None:
        """Handle provider degradation event."""
        severity = SecuritySeverity.MEDIUM
        if event.consecutive_failures >= self.CRITICAL_FAILURE_THRESHOLD:
            severity = SecuritySeverity.HIGH

        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.REPEATED_FAILURES,
                severity=severity,
                message=f"Provider degraded after {event.consecutive_failures} consecutive failures",
                provider_id=event.provider_id,
                details={
                    "consecutive_failures": event.consecutive_failures,
                    "total_failures": event.total_failures,
                    "reason": event.reason,
                },
                correlation_id=event.event_id,
            )
        )

    def _handle_tool_invocation_completed(self, event: ToolInvocationCompleted) -> None:
        """Handle successful tool invocation."""
        # Only log if unusually slow (potential DoS or resource exhaustion)
        if event.duration_ms > 10000:  # > 10 seconds
            self._emit(
                SecurityEvent(
                    event_type=SecurityEventType.ACCESS_GRANTED,
                    severity=SecuritySeverity.LOW,
                    message=f"Slow tool invocation: {event.tool_name}",
                    provider_id=event.provider_id,
                    tool_name=event.tool_name,
                    details={
                        "duration_ms": event.duration_ms,
                    },
                    correlation_id=event.event_id,
                )
            )

    def _handle_tool_invocation_failed(self, event: ToolInvocationFailed) -> None:
        """Handle failed tool invocation."""
        self._record_failure(event.provider_id)

        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.ACCESS_DENIED,
                severity=SecuritySeverity.LOW,
                message=f"Tool invocation failed: {event.tool_name}",
                provider_id=event.provider_id,
                tool_name=event.tool_name,
                details={
                    "error": event.error_message,
                },
                correlation_id=event.event_id,
            )
        )

    def _handle_health_check_failed(self, event: HealthCheckFailed) -> None:
        """Handle health check failure."""
        self._record_failure(event.provider_id)

        if event.consecutive_failures >= self.FAILURE_THRESHOLD:
            self._emit(
                SecurityEvent(
                    event_type=SecurityEventType.REPEATED_FAILURES,
                    severity=SecuritySeverity.MEDIUM,
                    message="Multiple health check failures for provider",
                    provider_id=event.provider_id,
                    details={
                        "consecutive_failures": event.consecutive_failures,
                        "error": event.error_message,
                    },
                    correlation_id=event.event_id,
                )
            )

    def _record_failure(self, provider_id: str) -> None:
        """Record a failure for anomaly detection."""
        now = time.time()
        with self._lock:
            if provider_id not in self._failure_counts:
                self._failure_counts[provider_id] = []

            # Add current failure
            self._failure_counts[provider_id].append(now)

            # Clean old entries
            cutoff = now - self.TIME_WINDOW_S
            self._failure_counts[provider_id] = [t for t in self._failure_counts[provider_id] if t > cutoff]

    def _check_anomalies(self, event: DomainEvent) -> None:
        """Check for anomalous patterns across events."""
        provider_id = getattr(event, "provider_id", None)
        if not provider_id:
            return

        with self._lock:
            failures = self._failure_counts.get(provider_id, [])

        if len(failures) >= self.CRITICAL_FAILURE_THRESHOLD:
            self._emit(
                SecurityEvent(
                    event_type=SecurityEventType.PROVIDER_COMPROMISE_SUSPECTED,
                    severity=SecuritySeverity.HIGH,
                    message="High failure rate detected for provider (possible attack or compromise)",
                    provider_id=provider_id,
                    details={
                        "failures_in_window": len(failures),
                        "window_seconds": self.TIME_WINDOW_S,
                    },
                )
            )

    def _emit(self, event: SecurityEvent) -> None:
        """Emit a security event to the sink."""
        try:
            self._sink.emit(event)
        except Exception as e:
            logger.error(f"Failed to emit security event: {e}")

    # --- Public API for direct security event emission ---

    def log_rate_limit_exceeded(
        self,
        provider_id: str | None = None,
        limit: int = 0,
        window_seconds: int = 0,
        source_ip: str | None = None,
    ) -> None:
        """Log a rate limit violation."""
        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.RATE_LIMIT_EXCEEDED,
                severity=SecuritySeverity.MEDIUM,
                message="Rate limit exceeded",
                provider_id=provider_id,
                source_ip=source_ip,
                details={
                    "limit": limit,
                    "window_seconds": window_seconds,
                },
            )
        )

    def log_validation_failed(
        self,
        field: str,
        message: str,
        provider_id: str | None = None,
        value: str | None = None,
    ) -> None:
        """Log a validation failure."""
        # Determine severity based on field
        severity = SecuritySeverity.LOW
        if field in ("command", "image"):
            severity = SecuritySeverity.MEDIUM

        details = {"field": field}
        if value:
            # Truncate value for safety
            details["value"] = value[:50] if len(value) > 50 else value

        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.VALIDATION_FAILED,
                severity=severity,
                message=f"Validation failed: {message}",
                provider_id=provider_id,
                details=details,
            )
        )

    def log_injection_attempt(
        self,
        field: str,
        pattern: str,
        provider_id: str | None = None,
        source_ip: str | None = None,
    ) -> None:
        """Log a potential injection attempt."""
        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.INJECTION_ATTEMPT,
                severity=SecuritySeverity.HIGH,
                message=f"Potential injection attempt detected in {field}",
                provider_id=provider_id,
                source_ip=source_ip,
                details={
                    "field": field,
                    "pattern_detected": pattern,
                },
            )
        )

    def log_suspicious_command(
        self,
        command: list[str],
        provider_id: str | None = None,
        reason: str = "",
    ) -> None:
        """Log a suspicious command execution attempt."""
        # Sanitize command for logging (don't log full values)
        safe_command = [c[:20] + "..." if len(c) > 20 else c for c in command[:5]]

        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.SUSPICIOUS_COMMAND,
                severity=SecuritySeverity.HIGH,
                message=f"Suspicious command blocked: {reason}",
                provider_id=provider_id,
                details={
                    "command_preview": safe_command,
                    "reason": reason,
                },
            )
        )

    def log_config_change(
        self,
        change_type: str,
        provider_id: str | None = None,
        user_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a configuration change."""
        self._emit(
            SecurityEvent(
                event_type=SecurityEventType.CONFIG_CHANGE,
                severity=SecuritySeverity.INFO,
                message=f"Configuration changed: {change_type}",
                provider_id=provider_id,
                user_id=user_id,
                details=details or {},
            )
        )

    @property
    def sink(self) -> SecurityEventSink:
        """Get the security event sink."""
        return self._sink


# --- Global security handler instance ---

_security_handler: SecurityEventHandler | None = None


def get_security_handler(
    sink: SecurityEventSink | None = None,
) -> SecurityEventHandler:
    """Get or create the global security handler instance."""
    global _security_handler
    if _security_handler is None:
        _security_handler = SecurityEventHandler(sink=sink)
    return _security_handler


def reset_security_handler() -> None:
    """Reset the global security handler (for testing)."""
    global _security_handler
    _security_handler = None
