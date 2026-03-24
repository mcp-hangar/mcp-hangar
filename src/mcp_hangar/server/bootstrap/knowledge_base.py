"""Knowledge base initialization."""

import asyncio
from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)


def init_knowledge_base(config: dict[str, Any]) -> None:
    """Initialize knowledge base from config.yaml.

    Supports multiple drivers (postgres, sqlite, memory) with auto-detection.

    Args:
        config: Full configuration dictionary.
    """
    from ...infrastructure.knowledge_base import init_knowledge_base as kb_init, KnowledgeBaseConfig

    kb_config_dict = config.get("knowledge_base", {})
    kb_config = KnowledgeBaseConfig.from_dict(kb_config_dict)

    if not kb_config.enabled:
        logger.info("knowledge_base_disabled")
        return

    # Initialize asynchronously
    async def init():
        kb = await kb_init(kb_config)
        if kb:
            # Verify health
            healthy = await kb.is_healthy()
            if healthy:
                logger.info("knowledge_base_health_ok")
            else:
                logger.warning("knowledge_base_health_check_failed")

    asyncio.run(init())
