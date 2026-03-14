"""Event handlers registration."""

from typing import TYPE_CHECKING

from ...application.event_handlers import (
    LoggingEventHandler,
    MetricsEventHandler,
    get_alert_handler,
    get_audit_handler,
)
from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_event_handlers(runtime: "Runtime") -> None:
    """Register all event handlers.

    Args:
        runtime: Runtime instance with event bus.
    """
    logging_handler = LoggingEventHandler()
    runtime.event_bus.subscribe_to_all(logging_handler.handle)

    metrics_handler = MetricsEventHandler()
    runtime.event_bus.subscribe_to_all(metrics_handler.handle)

    alert_handler = get_alert_handler()
    runtime.event_bus.subscribe_to_all(alert_handler.handle)

    audit_handler = get_audit_handler()
    runtime.event_bus.subscribe_to_all(audit_handler.handle)

    runtime.event_bus.subscribe_to_all(runtime.security_handler.handle)

    # Knowledge base handler (PostgreSQL persistence)
    from ...application.event_handlers.knowledge_base_handler import KnowledgeBaseEventHandler

    kb_handler = KnowledgeBaseEventHandler()
    runtime.event_bus.subscribe_to_all(kb_handler.handle)

    logger.info(
        "event_handlers_registered",
        handlers=["logging", "metrics", "alert", "audit", "security", "knowledge_base"],
    )
