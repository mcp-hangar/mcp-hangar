"""Event handlers for reacting to domain events."""

from .alert_handler import Alert, AlertEventHandler, AlertSink, CallbackAlertSink, LogAlertSink
from .audit_handler import AuditEventHandler, AuditRecord, AuditStore, InMemoryAuditStore, LogAuditStore
from .logging_handler import LoggingEventHandler
from .metrics_handler import MetricsEventHandler
from .security_handler import (
    CallbackSecuritySink,
    CompositeSecuritySink,
    InMemorySecuritySink,
    LogSecuritySink,
    SecurityEvent,
    SecurityEventHandler,
    SecurityEventSink,
    SecurityEventType,
    SecuritySeverity,
    get_security_handler,
    reset_security_handler,
)

__all__ = [
    # Logging
    "LoggingEventHandler",
    # Metrics
    "MetricsEventHandler",
    # Alerts
    "AlertEventHandler",
    "Alert",
    "AlertSink",
    "LogAlertSink",
    "CallbackAlertSink",
    # Audit
    "AuditEventHandler",
    "AuditRecord",
    "AuditStore",
    "InMemoryAuditStore",
    "LogAuditStore",
    # Security
    "SecurityEventHandler",
    "SecurityEvent",
    "SecurityEventType",
    "SecuritySeverity",
    "SecurityEventSink",
    "LogSecuritySink",
    "InMemorySecuritySink",
    "CallbackSecuritySink",
    "CompositeSecuritySink",
    "get_security_handler",
    "reset_security_handler",
]
