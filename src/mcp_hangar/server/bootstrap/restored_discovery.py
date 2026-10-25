"""Discovered servers stay discovery's across a restart (#2106).

On a durable backend the fleet is restored before discovery starts. Seeding
"static always wins" from every id in the repository then made every server
discovery had registered static: an authoritative source never removed it
again, a changed definition was never applied, and the protection meant for
what an operator declared covered a server nobody declared.

What is static is what discovery cannot claim: the configuration file's servers
and those created over the API or the CLI, which carry HUMAN provenance (as a
row written before provenance was recorded reads back), and a discovered server
whose source cannot be told. A discovered server is handed back to the source it
belongs to, and to no other.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...application.commands.crud_commands import AdoptDiscoveredMcpServerCommand
from ...application.discovery import DiscoveryOrchestrator
from ...domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from ...domain.exceptions import McpServerNotFoundError, ValidationError
from ...domain.value_objects.provenance import DiscoveryOrigin
from ...logging_config import get_logger

logger = get_logger(__name__)


def declared_mcp_server_ids(repository: Any) -> set[str]:
    """The servers discovery must not touch: all but those a discovery source can claim."""
    return {name for name in repository.get_all_ids() if _discovering_source(repository.get(name)) is None}


def _discovering_source(server: Any) -> str | None:
    return getattr(server, "discovering_source", None) if server is not None else None


def discovery_origin_of(mcp_server: DiscoveredMcpServer) -> DiscoveryOrigin | None:
    """What the row records of a discovered server, to take it back after a restart.

    None when the report lacks a source or a fingerprint: the row then reads
    as one written before the origin was recorded.
    """
    metadata = getattr(mcp_server, "metadata", None)
    namespace = metadata.get("namespace") if isinstance(metadata, dict) else None
    return DiscoveryOrigin.from_dict(
        {
            "source_type": getattr(mcp_server, "source_type", None),
            "fingerprint": getattr(mcp_server, "fingerprint", None),
            "namespace": namespace,
        }
    )


def govern_restored_discovered_servers(
    orchestrator: DiscoveryOrchestrator, repository: Any, send: Callable[[Any], Any]
) -> None:
    """Hand the discovered servers already in the fleet back to *orchestrator*.

    A row that records its origin is tracked under the source that registered
    it, from the start of each management tenure (see
    `DiscoveryOrchestrator._take_back_restored`).

    A row written before the origin was recorded is attributed by the
    description discovery gave it, and taken over when that source next reports
    it: the registration the report sends is answered by adopting the server,
    which writes the origin to the row (#2112), instead of a create refused as
    "already exists" on every cycle. Another source reporting the name is
    refused, as is every source for a row whose source cannot be told; that one
    is static. A changed definition of a server discovery tracks goes on as
    before.

    Args:
        orchestrator: The orchestrator, its callbacks already set.
        repository: The fleet.
        send: Sends a command on the command bus.
    """

    def restored() -> list[DiscoveredMcpServer]:
        servers = []
        for name in repository.get_all_ids():
            server = repository.get(name)
            origin = getattr(server, "discovered_as", None) if _discovering_source(server) else None
            if origin is None:
                continue
            servers.append(
                DiscoveredMcpServer.restored(
                    name=name,
                    source_type=origin.source_type,
                    mode=server.mode_str,
                    fingerprint=origin.fingerprint,
                    metadata={"namespace": origin.namespace} if origin.namespace else None,
                    ttl_seconds=orchestrator.config.default_ttl_s,
                )
            )
        return servers

    orchestrator.restored_servers = restored

    register = orchestrator.on_register
    if register is None:
        return
    refused: set[tuple[str, str]] = set()

    async def adopt_or_register(mcp_server: DiscoveredMcpServer) -> bool:
        if _discovering_source(repository.get(mcp_server.name)) is None or orchestrator.is_tracking(mcp_server.name):
            return await register(mcp_server)
        origin = discovery_origin_of(mcp_server)
        if origin is None:
            return False
        try:
            send(AdoptDiscoveredMcpServerCommand(mcp_server_id=mcp_server.name, discovered_as=origin))
        except (McpServerNotFoundError, ValidationError) as e:
            # Repeated every cycle the source reports the name; said once.
            if (mcp_server.name, origin.source_type) not in refused:
                refused.add((mcp_server.name, origin.source_type))
                logger.warning(
                    "discovery_takeover_refused",
                    mcp_server_name=mcp_server.name,
                    source_type=origin.source_type,
                    detail=str(e),
                )
            return False
        return True

    orchestrator.on_register = adopt_or_register
