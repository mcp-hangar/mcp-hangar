"""Knowledge Base package.

Provides pluggable storage backends for:
- Tool result caching
- Audit logging
- Provider state tracking
- Metrics

Supported drivers:
- PostgreSQL (requires asyncpg)
- SQLite (uses aiosqlite, included)
- Memory (for testing)

Usage:
    from mcp_hangar.infrastructure.knowledge_base import (
        get_knowledge_base,
        init_knowledge_base,
        KnowledgeBaseConfig,
    )

    # Initialize from config
    config = KnowledgeBaseConfig.from_dict({
        "enabled": True,
        "dsn": "postgresql://user:pass@localhost/db",
    })
    kb = await init_knowledge_base(config)

    # Use
    await kb.audit_log(AuditEntry(event_type="test"))
    await kb.cache_set("math", "add", {"a": 1}, {"result": 2})
"""

from ...logging_config import get_logger
from .contracts import (
    AuditEntry,
    IKnowledgeBase,
    KnowledgeBaseConfig,
    KnowledgeBaseDriver,
    MetricEntry,
    ProviderStateEntry,
)

logger = get_logger(__name__)

# Global instance
_instance: IKnowledgeBase | None = None
_config: KnowledgeBaseConfig | None = None


def get_knowledge_base() -> IKnowledgeBase | None:
    """Get the global knowledge base instance.

    Returns None if not initialized or disabled.
    """
    return _instance


def get_config() -> KnowledgeBaseConfig | None:
    """Get current knowledge base configuration."""
    return _config


def is_available() -> bool:
    """Check if knowledge base is available and healthy."""
    return _instance is not None


async def init_knowledge_base(config: KnowledgeBaseConfig) -> IKnowledgeBase | None:
    """Initialize knowledge base from configuration.

    Creates appropriate driver based on config and runs migrations.

    Args:
        config: Knowledge base configuration

    Returns:
        Initialized knowledge base instance, or None if disabled/failed
    """
    global _instance, _config

    if not config.enabled:
        logger.info("knowledge_base_disabled")
        return None

    _config = config

    # Create driver based on config
    if config.driver == KnowledgeBaseDriver.POSTGRES:
        from .postgres import PostgresKnowledgeBase

        _instance = PostgresKnowledgeBase(config)

    elif config.driver == KnowledgeBaseDriver.SQLITE:
        from .sqlite import SQLiteKnowledgeBase

        _instance = SQLiteKnowledgeBase(config)

    elif config.driver == KnowledgeBaseDriver.MEMORY:
        from .memory import MemoryKnowledgeBase

        _instance = MemoryKnowledgeBase(config)

    else:
        logger.error("unknown_kb_driver", driver=config.driver)
        return None

    # Initialize (runs migrations)
    success = await _instance.initialize()

    if not success:
        logger.error("knowledge_base_init_failed", driver=config.driver.value)
        _instance = None
        return None

    # Mask password in DSN for logging
    dsn = config.dsn
    if "@" in dsn:
        parts = dsn.split("@")
        masked_dsn = parts[0].rsplit(":", 1)[0] + ":***@" + parts[1]
    else:
        masked_dsn = dsn

    logger.info(
        "knowledge_base_ready",
        driver=config.driver.value,
        dsn=masked_dsn,
    )

    return _instance


async def close_knowledge_base() -> None:
    """Close knowledge base and cleanup resources."""
    global _instance, _config

    if _instance:
        await _instance.close()
        _instance = None

    _config = None


# Convenience functions that use global instance


async def audit_log(
    event_type: str,
    provider: str | None = None,
    tool: str | None = None,
    arguments: dict | None = None,
    result_summary: str | None = None,
    duration_ms: int | None = None,
    success: bool = True,
    error_message: str | None = None,
    correlation_id: str | None = None,
) -> bool:
    """Log audit entry using global instance."""
    if not _instance:
        return False

    return await _instance.audit_log(
        AuditEntry(
            event_type=event_type,
            provider=provider,
            tool=tool,
            arguments=arguments,
            result_summary=result_summary,
            duration_ms=duration_ms,
            success=success,
            error_message=error_message,
            correlation_id=correlation_id,
        )
    )


async def record_state_change(
    provider_id: str,
    old_state: str | None,
    new_state: str,
    reason: str | None = None,
) -> bool:
    """Record provider state change using global instance."""
    if not _instance:
        return False

    return await _instance.record_state_change(
        ProviderStateEntry(
            provider_id=provider_id,
            old_state=old_state,
            new_state=new_state,
            reason=reason,
        )
    )


async def record_metric(
    provider_id: str,
    metric_name: str,
    metric_value: float,
    labels: dict | None = None,
) -> bool:
    """Record provider metric using global instance."""
    if not _instance:
        return False

    return await _instance.record_metric(
        MetricEntry(
            provider_id=provider_id,
            metric_name=metric_name,
            metric_value=metric_value,
            labels=labels,
        )
    )


async def cache_get(provider: str, tool: str, arguments: dict) -> dict | None:
    """Get cached result using global instance."""
    if not _instance:
        return None
    return await _instance.cache_get(provider, tool, arguments)


async def cache_set(
    provider: str,
    tool: str,
    arguments: dict,
    result: dict,
    ttl_s: int | None = None,
) -> bool:
    """Set cached result using global instance."""
    if not _instance:
        return False
    return await _instance.cache_set(provider, tool, arguments, result, ttl_s)


__all__ = [
    # Config
    "KnowledgeBaseConfig",
    "KnowledgeBaseDriver",
    # Contracts
    "IKnowledgeBase",
    "AuditEntry",
    "ProviderStateEntry",
    "MetricEntry",
    # Instance management
    "init_knowledge_base",
    "close_knowledge_base",
    "get_knowledge_base",
    "get_config",
    "is_available",
    # Convenience functions
    "audit_log",
    "record_state_change",
    "record_metric",
    "cache_get",
    "cache_set",
]
