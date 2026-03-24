"""Event handlers for reacting to domain events."""

from .alert_handler import (
    Alert,
    AlertEventHandler,
    AlertSink,
    CallbackAlertSink,
    get_alert_handler,
    LogAlertSink,
    reset_alert_handler,
)
from .audit_handler import (
    AuditEventHandler,
    AuditRecord,
    AuditStore,
    get_audit_handler,
    InMemoryAuditStore,
    LogAuditStore,
    reset_audit_handler,
)
from .logging_handler import LoggingEventHandler
from .metrics_handler import MetricsEventHandler
from .security_handler import (
    CallbackSecuritySink,
    CompositeSecuritySink,
    get_security_handler,
    InMemorySecuritySink,
    LogSecuritySink,
    reset_security_handler,
    SecurityEvent,
    SecurityEventHandler,
    SecurityEventSink,
    SecurityEventType,
    SecuritySeverity,
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
    "get_alert_handler",
    "reset_alert_handler",
    # Audit
    "AuditEventHandler",
    "AuditRecord",
    "AuditStore",
    "InMemoryAuditStore",
    "LogAuditStore",
    "get_audit_handler",
    "reset_audit_handler",
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
