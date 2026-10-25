"""Discovered servers stay discovery's across a restart (#2106).

On a durable backend the fleet is restored before discovery starts. Seeding
"static always wins" from every id in the repository then made every server
discovery had registered static: an authoritative source never removed it
again, a changed definition was never applied, and the protection meant for
what an operator declared covered a server nobody declared.

What is static is what a human declared: the configuration file's servers and
those created over the API or the CLI, which all carry HUMAN provenance. A row
written before provenance was recorded reads back HUMAN, so it stays protected
as it was. Only a DISCOVERY server is handed back to discovery.
"""

from __future__ import annotations

from typing import Any

from ...application.discovery import DiscoveryOrchestrator
from ...domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from ...domain.value_objects.provenance import DiscoveryOrigin, Provenance
from ...logging_config import get_logger

logger = get_logger(__name__)


def declared_mcp_server_ids(repository: Any) -> set[str]:
    """The servers discovery must not touch: everything but what discovery registered."""
    return {name for name in repository.get_all_ids() if not _is_discovered(repository.get(name))}


def _is_discovered(server: Any) -> bool:
    return server is not None and server.provenance is Provenance.DISCOVERY


def discovery_origin_of(mcp_server: DiscoveredMcpServer) -> DiscoveryOrigin | None:
    """What the row records of a discovered server, to take it back after a restart.

    None when the report lacks a source or a fingerprint: the row then reads
    as one written before the origin was recorded, which is taken over when a
    source next reports it.
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


def govern_restored_discovered_servers(orchestrator: DiscoveryOrchestrator, repository: Any) -> None:
    """Hand the DISCOVERY servers already in the fleet back to *orchestrator*.

    A row that records its origin is tracked under the source that registered
    it, from the start of each management tenure (see
    `DiscoveryOrchestrator._take_back_restored`). A row that does not --
    written before the origin was recorded -- cannot be attributed to any
    source, so no source can be said to have lost it; it is taken over when a
    source next reports it, and stays until then, as on an additive source.

    Taking one over means answering the registration that report would send:
    the server is already in the fleet, so creating it again is refused, and a
    refused registration is never tracked. Only for a name discovery does not
    already govern: a changed definition of one it does goes on as before.
    """

    def restored() -> list[DiscoveredMcpServer]:
        servers = []
        for name in repository.get_all_ids():
            server = repository.get(name)
            if not _is_discovered(server) or server.discovered_as is None:
                continue
            origin = server.discovered_as
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

    async def take_over_or_register(mcp_server: DiscoveredMcpServer) -> bool:
        if _is_discovered(repository.get(mcp_server.name)) and not orchestrator.is_tracking(mcp_server.name):
            logger.info(
                "discovery_took_over_restored_server",
                mcp_server_name=mcp_server.name,
                source_type=mcp_server.source_type,
            )
            return True
        return await register(mcp_server)

    orchestrator.on_register = take_over_or_register
