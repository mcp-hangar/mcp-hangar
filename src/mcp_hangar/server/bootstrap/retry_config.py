"""Retry configuration initialization."""

from typing import Any

from ...logging_config import get_logger
from ...retry import get_retry_store

logger = get_logger(__name__)


def init_retry_config(config: dict[str, Any]) -> None:
    """Initialize retry configuration from config.yaml.

    Args:
        config: Full configuration dictionary.
    """
    retry_store = get_retry_store()
    retry_store.load_from_config(config)
    logger.info("retry_config_loaded")
