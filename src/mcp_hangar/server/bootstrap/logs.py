"""Wiring for per-mcp_server log buffers.

Every server in the repository holds one, however it got there. Bootstrap
attaches one to each server the file declared, at boot; a configuration commit
attaches one to each server it ADDS and releases the one a server it removed
held (`_StagedConfig.commit` in `server/config.py`). Both go through the
helpers here, so the sizing and the wiring are written once (#1502).

A buffer is created, registered in the singleton registry that the
``GET /api/mcp_servers/{id}/logs`` endpoint reads, and injected into the
:class:`~mcp_hangar.domain.model.mcp_server.McpServer` aggregate, which fills it
from the server process's stderr as soon as that process starts.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.model import McpServer
from ...logging_config import get_logger

logger = get_logger(__name__)


def ensure_log_buffer(mcp_server_id: str, mcp_server: McpServer) -> bool:
    """Give *mcp_server* a log buffer unless it already holds one.

    1. Creates a :class:`~mcp_hangar.infrastructure.persistence.log_buffer.McpServerLogBuffer`.
    2. Registers it in the singleton registry via
       :func:`~mcp_hangar.infrastructure.persistence.log_buffer.set_log_buffer`.
    3. Injects it into the aggregate via
       :meth:`~mcp_hangar.domain.model.mcp_server.McpServer.set_log_buffer`.

    A server that already holds one keeps it, with the lines already in it. A
    rebuilt server carries its predecessor's over (#1498), and a running
    server's stderr reader holds the buffer it was started with: replacing the
    registered one would leave the reader filling a buffer no API reads.

    Args:
        mcp_server_id: The id the buffer is registered under, and the one the
            logs endpoint looks it up by.
        mcp_server: The aggregate to inject it into.

    Returns:
        Whether a buffer was attached.
    """
    # Imported lazily to avoid circular imports between bootstrap sub-modules.
    from ...infrastructure.persistence.log_buffer import McpServerLogBuffer, set_log_buffer

    if mcp_server._log_buffer is not None:
        return False

    buffer = McpServerLogBuffer(mcp_server_id=mcp_server_id)
    set_log_buffer(mcp_server_id, buffer)
    mcp_server.set_log_buffer(buffer)
    return True


def release_log_buffer(mcp_server_id: str) -> None:
    """Drop the buffer registered for a server that has left the repository.

    The registry is a process-wide dict, and removing a server from the
    repository does not reach it: a reload that removed a server left its
    output registered under that id for the life of the process (#1502).

    Args:
        mcp_server_id: The id whose registry entry is dropped. Unknown ids are
            ignored, so this is safe to call for a server that never had one.
    """
    from ...infrastructure.persistence.log_buffer import remove_log_buffer

    remove_log_buffer(mcp_server_id)


def init_log_buffers(mcp_servers: Mapping[str, McpServer]) -> None:
    """Attach a log buffer to every mcp_server in *mcp_servers* that holds none.

    Bootstrap's pass over the servers the configuration built. It is a backstop
    rather than the only wiring: the commit attaches a buffer to every server it
    puts in the repository, so by the time this runs each of them already holds
    one and keeps it.

    Args:
        mcp_servers: Dict-like mapping of mcp_server_id -> McpServer aggregate
            instance. Typically the shared runtime repository from
            ``server.bootstrap.composition.get_runtime()``.
    """
    mcp_server_ids = list(mcp_servers.keys())
    attached = [
        mcp_server_id
        for mcp_server_id, mcp_server in mcp_servers.items()
        if mcp_server is not None and ensure_log_buffer(mcp_server_id, mcp_server)
    ]

    logger.info(
        "log_buffers_initialized",
        mcp_server_count=len(mcp_server_ids),
        mcp_server_ids=mcp_server_ids,
        attached=attached,
    )
