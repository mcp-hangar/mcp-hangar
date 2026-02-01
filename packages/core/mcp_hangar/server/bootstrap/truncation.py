"""Truncation system bootstrap.

Initializes the response truncation system for batch invocations,
including cache backend selection and manager configuration.
"""

from typing import Any

from ...domain.contracts.response_cache import IResponseCache, NullResponseCache
from ...domain.value_objects.truncation import TruncationConfig
from ...infrastructure.truncation.manager import TruncationManager
from ...infrastructure.truncation.memory_cache import MemoryResponseCache
from ...logging_config import get_logger

logger = get_logger(__name__)

# Global singleton instances
_response_cache: IResponseCache | None = None
_truncation_manager: TruncationManager | None = None


def get_truncation_manager() -> TruncationManager | None:
    """Get the global truncation manager.

    Returns:
        The truncation manager if initialized and enabled, None otherwise.
    """
    return _truncation_manager


def get_response_cache() -> IResponseCache | None:
    """Get the global response cache.

    Returns:
        The response cache if initialized, None otherwise.
    """
    return _response_cache


def init_truncation(config: dict[str, Any]) -> TruncationManager | None:
    """Initialize the truncation system.

    Creates the appropriate cache backend and truncation manager
    based on configuration.

    Args:
        config: Full application configuration dictionary.

    Returns:
        TruncationManager if truncation is enabled, None otherwise.
    """
    global _response_cache, _truncation_manager

    truncation_config = TruncationConfig.from_dict(config.get("truncation"))

    if not truncation_config.enabled:
        logger.debug("truncation_disabled")
        _response_cache = NullResponseCache()
        _truncation_manager = None
        return None

    # Create cache backend
    if truncation_config.cache_driver == "redis":
        try:
            from ...infrastructure.truncation.redis_cache import RedisResponseCache

            _response_cache = RedisResponseCache(truncation_config.redis_url)
            logger.info(
                "truncation_redis_cache_initialized",
                max_batch_size=truncation_config.max_batch_size_bytes,
            )
        except ImportError:
            logger.warning(
                "truncation_redis_unavailable",
                message="redis package not installed, falling back to memory cache",
            )
            _response_cache = MemoryResponseCache(
                max_entries=truncation_config.max_cache_entries,
                default_ttl_s=truncation_config.cache_ttl_s,
            )
        except Exception as e:
            logger.error(
                "truncation_redis_init_failed",
                error=str(e),
                message="Falling back to memory cache",
            )
            _response_cache = MemoryResponseCache(
                max_entries=truncation_config.max_cache_entries,
                default_ttl_s=truncation_config.cache_ttl_s,
            )
    else:
        _response_cache = MemoryResponseCache(
            max_entries=truncation_config.max_cache_entries,
            default_ttl_s=truncation_config.cache_ttl_s,
        )
        logger.info(
            "truncation_memory_cache_initialized",
            max_entries=truncation_config.max_cache_entries,
            ttl_s=truncation_config.cache_ttl_s,
        )

    # Create truncation manager
    _truncation_manager = TruncationManager(truncation_config, _response_cache)

    logger.info(
        "truncation_manager_initialized",
        enabled=True,
        max_batch_size=truncation_config.max_batch_size_bytes,
        min_per_response=truncation_config.min_per_response_bytes,
        cache_driver=truncation_config.cache_driver,
        preserve_json=truncation_config.preserve_json_structure,
    )

    return _truncation_manager


def reset_truncation() -> None:
    """Reset the truncation system.

    Primarily for testing purposes.
    """
    global _response_cache, _truncation_manager
    _response_cache = None
    _truncation_manager = None
