"""A group member follows its server out of the fleet and back in (#2107).

A group member holds its server by reference. When the server is deleted -- over
the API, by discovery, or on a peer by the fleet projection -- every group that
has it as a member is told, so none of them routes a call to the stopped copy
and starts it again where nothing manages it. The member stays, vacant: members
are declared by id, as in the configuration file. When a server is registered
under that id again, the member holds the new copy.

Both the command handlers and the fleet projection go through here, so the
replica that deleted a server and the replicas that learned of it agree.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.model import McpServer, McpServerGroup
from ..domain.value_objects.peer_identity import PeerIdentity
from ..logging_config import get_logger

logger = get_logger(__name__)


def vacate_member(groups: Mapping[str, McpServerGroup] | None, mcp_server_id: str) -> list[McpServerGroup]:
    """Leave *mcp_server_id*'s member in every group without a server.

    Returns:
        The groups that have it as a member, whose events the caller drains.
    """
    return [group for group in list((groups or {}).values()) if group.vacate_member(mcp_server_id)]


def fill_vacant_member(
    groups: Mapping[str, McpServerGroup] | None, mcp_server_id: str, mcp_server: McpServer
) -> list[McpServerGroup]:
    """Point every vacant member of id *mcp_server_id* at *mcp_server*, the server registered under it again.

    Not a key-pinned server: one cannot join a group (ADR-030 §8), and filling
    a member would let it in without the check adding a member makes. The
    member stays vacant.

    Returns:
        The groups whose member was filled, whose events the caller drains.
    """
    if not groups:
        return []
    if isinstance(getattr(mcp_server, "_peer_identity", None), PeerIdentity):
        if any(group.get_member(mcp_server_id) is not None for group in list(groups.values())):
            logger.warning(
                "group_member_left_vacant",
                mcp_server_id=mcp_server_id,
                reason="a key-pinned server cannot join a group",
            )
        return []
    return [group for group in list(groups.values()) if group.fill_vacant_member(mcp_server_id, mcp_server)]
