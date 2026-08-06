"""Turning a live server into the record that can rebuild it.

One definition, because there were about to be two. `RecoveryService` already
built this snapshot -- reaching into a dozen of the aggregate's private
attributes to do it -- and the registration path now needs the same thing. Two
copies of "what a server's configuration is" drift the moment a field is added,
and the drift is invisible: the field is simply absent after a restart.

The reverse direction (`_create_mcp_server_from_config`) stays where it is, in
recovery, since only recovery constructs aggregates from records.
"""

from ..contracts.persistence import McpServerConfigSnapshot
from ..model import McpServer


def snapshot_of(mcp_server: McpServer, *, enabled: bool = True) -> McpServerConfigSnapshot:
    """Capture a server's configuration as it should be restored.

    Args:
        mcp_server: The live aggregate.
        enabled: Whether recovery should bring this server back. False is how a
            server stays on record without being restored.

    Returns:
        The snapshot to persist.
    """
    return McpServerConfigSnapshot(
        mcp_server_id=mcp_server.mcp_server_id,
        mode=mcp_server.mode_str,
        command=mcp_server._command,
        image=mcp_server._image,
        endpoint=mcp_server._endpoint,
        env=mcp_server._env,
        idle_ttl_s=mcp_server._idle_ttl.seconds,
        health_check_interval_s=mcp_server._health_check_interval.seconds,
        max_consecutive_failures=mcp_server._health.max_consecutive_failures,
        description=mcp_server.description,
        volumes=mcp_server._volumes,
        build=mcp_server._build,
        resources=mcp_server._resources,
        network=mcp_server._network,
        read_only=mcp_server._read_only,
        user=mcp_server._user,
        # Only servers whose tools were declared in configuration: for anything
        # else the tool list is what the server reported at startup, and
        # restoring a stale copy of that would answer for a running server
        # without asking it.
        tools=([tool.to_dict() for tool in mcp_server.tools] if mcp_server._tools_predefined else None),
        enabled=enabled,
    )
