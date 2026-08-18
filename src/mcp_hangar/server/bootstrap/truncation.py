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

    # Create cache backend. FAIL CLOSED (#1007): when the operator asked for
    # Redis, a missing package, a bad URL, or a server that cannot SETEX must
    # refuse the boot -- the old memory fallback produced a gateway whose logs
    # said `cache_driver=redis` while every cross-replica continuation missed.
    if truncation_config.cache_driver == "redis":
        from ...infrastructure.truncation.redis_cache import RedisResponseCache

        assert truncation_config.redis_url is not None
        _response_cache = RedisResponseCache(truncation_config.redis_url)
        cache_backend = "redis"
    else:
        _response_cache = MemoryResponseCache(
            max_entries=truncation_config.max_cache_entries,
            default_ttl_s=truncation_config.cache_ttl_s,
        )
        cache_backend = "memory"
        if config.get("coordination"):
            # Legal (truncation is opt-in), but a continuation minted on one
            # replica is unfetchable on any other -- worth one line at boot.
            logger.warning(
                "truncation_memory_cache_is_per_replica",
                message="cache_driver: memory on a coordinated deploy -- a continuation "
                "is only fetchable on the replica that truncated; use cache_driver: redis",
            )

    # Create truncation manager
    _truncation_manager = TruncationManager(truncation_config, _response_cache)

    logger.info(
        "truncation_manager_initialized",
        enabled=True,
        max_batch_size=truncation_config.max_batch_size_bytes,
        min_per_response=truncation_config.min_per_response_bytes,
        # The ACTUAL backend serving requests, not the configured wish.
        cache_backend=cache_backend,
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
