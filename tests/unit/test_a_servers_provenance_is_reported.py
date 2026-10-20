"""A server's provenance is in its details (#1977).

The operator moves a server core discovered from its own pod to a key-pinned
registration (ADR-030 §4f). Without the provenance it inferred that from its
own Deployment; with it, it can refuse to move a server a human registered.
"""

import pytest

from mcp_hangar.application.queries.handlers import GetMcpServerHandler
from mcp_hangar.application.queries.queries import GetMcpServerQuery
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provenance import Provenance


@pytest.mark.parametrize(("provenance", "reported"), [(Provenance.HUMAN, "human"), (Provenance.DISCOVERY, "discovery")])
def test_the_details_name_how_the_server_reached_the_fleet(provenance, reported) -> None:
    fleet = InMemoryMcpServerRepository()
    fleet.add(
        "payments",
        McpServer(mcp_server_id="payments", mode="remote", endpoint="https://203.0.113.5:8443", provenance=provenance),
    )

    details = GetMcpServerHandler(fleet).handle(GetMcpServerQuery(mcp_server_id="payments")).to_dict()

    assert details["provenance"] == reported
