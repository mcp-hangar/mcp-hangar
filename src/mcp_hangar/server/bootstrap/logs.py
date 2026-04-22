"""Bootstrap wiring for per-mcp_server log buffers.

Called once during bootstrap after mcp_servers have been loaded from config.
Creates a McpServerLogBuffer per mcp_server, registers each buffer in the
singleton registry, and injects it into the McpServer aggregate via
McpServer.set_log_buffer().
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.model import McpServer
from ...logging_config import get_logger

logger = get_logger(__name__)


def init_log_buffers(mcp_servers: Mapping[str, McpServer]) -> None:
    """Create and wire log buffers for all loaded mcp_servers.

    For each mcp_server in *mcp_servers*:
    1. Creates a :class:`~mcp_hangar.infrastructure.persistence.log_buffer.McpServerLogBuffer`.
    2. Registers the buffer in the singleton registry via
       :func:`~mcp_hangar.infrastructure.persistence.log_buffer.set_log_buffer`.
    3. Injects the buffer into the :class:`~mcp_hangar.domain.model.mcp_server.McpServer`
       aggregate via :meth:`~mcp_hangar.domain.model.mcp_server.McpServer.set_log_buffer`.

    This function is idempotent -- calling it again replaces existing buffers.

    Args:
        mcp_servers: Dict-like mapping of mcp_server_id -> McpServer aggregate instance.
            Typically the shared runtime repository from
            ``server.bootstrap.composition.get_runtime()``.
    """
    # Import lazily to avoid circular imports between bootstrap sub-modules.
    from ...infrastructure.persistence.log_buffer import McpServerLogBuffer, set_log_buffer

    mcp_server_ids = list(mcp_servers.keys())

    for mcp_server_id in mcp_server_ids:
        mcp_server = mcp_servers.get(mcp_server_id)
        if mcp_server is None:
            continue

        buffer = McpServerLogBuffer(
            mcp_server_id=mcp_server_id,
        )
        set_log_buffer(mcp_server_id, buffer)
        mcp_server.set_log_buffer(buffer)

    logger.info("log_buffers_initialized", mcp_server_count=len(mcp_server_ids), mcp_server_ids=mcp_server_ids)
