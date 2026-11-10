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
from ...application.discovery import DiscoveryChangeRefused, DiscoveryOrchestrator, DiscoveryServerGone
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

    A row that records its origin and is not tracked -- its deregistration did
    not apply, say -- is taken back by its source the same way. When the
    reported fingerprint is not the recorded one, the reported definition
    replaces the running one, as a change to a tracked server does. It is never
    adopted under the new fingerprint, which would leave the old definition
    running while discovery took the new one as applied. A refused change
    leaves it tracked as the definition it runs, so its source still governs it.

    Args:
        orchestrator: The orchestrator, its callbacks already set.
        repository: The fleet.
        send: Sends a command on the command bus.
    """

    def restored() -> list[DiscoveredMcpServer]:
        ttl = orchestrator.config.default_ttl_s
        servers = (_restored_one(name, repository.get(name), ttl) for name in repository.get_all_ids())
        return [server for server in servers if server is not None]

    orchestrator.restored_servers = restored

    register = orchestrator.on_register
    if register is None:
        return
    orchestrator.on_register = _TakeBack(orchestrator, repository, send, register).adopt_or_register


def _restored_one(name: str, server: Any, ttl_s: int) -> DiscoveredMcpServer | None:
    """The definition a discovered server in the fleet runs, as its recorded origin says; None without one."""
    origin = getattr(server, "discovered_as", None) if _discovering_source(server) else None
    if origin is None:
        return None
    return DiscoveredMcpServer.restored(
        name=name,
        source_type=origin.source_type,
        mode=server.mode_str,
        fingerprint=origin.fingerprint,
        metadata={"namespace": origin.namespace} if origin.namespace else None,
        ttl_seconds=ttl_s,
    )


class _TakeBack:
    """Answers a source's report of a discovered server that is in the fleet and not tracked."""

    def __init__(
        self,
        orchestrator: DiscoveryOrchestrator,
        repository: Any,
        send: Callable[[Any], Any],
        register: Callable[[DiscoveredMcpServer], Any],
    ) -> None:
        self._orchestrator = orchestrator
        self._repository = repository
        self._send = send
        self._register = register
        self._refused: set[tuple[str, str]] = set()

    async def adopt_or_register(self, mcp_server: DiscoveredMcpServer) -> bool:
        server = self._repository.get(mcp_server.name)
        if _discovering_source(server) is None or self._orchestrator.is_tracking(mcp_server.name):
            return bool(await self._register(mcp_server))
        origin = discovery_origin_of(mcp_server)
        if origin is None:
            return False
        recorded = getattr(server, "discovered_as", None)
        if (
            recorded is not None
            and recorded.source_type == origin.source_type
            and recorded.fingerprint != origin.fingerprint
        ):
            return await self._apply_reported(mcp_server, origin)
        try:
            self._send(AdoptDiscoveredMcpServerCommand(mcp_server_id=mcp_server.name, discovered_as=origin))
        except (McpServerNotFoundError, ValidationError) as e:
            return self._refuse_once(mcp_server, origin, str(e))
        return True

    async def _apply_reported(self, mcp_server: DiscoveredMcpServer, origin: DiscoveryOrigin) -> bool:
        """Replace the running server with the reported definition, which differs from the one it runs.

        Adopting would record the reported fingerprint over the running
        definition, and discovery would then take the definition as applied
        and never apply it.
        """
        update = self._orchestrator.on_update
        if update is None:
            return self._keep_running(mcp_server, origin, "the reported definition cannot be applied")
        try:
            await update(mcp_server)
        except DiscoveryChangeRefused as e:
            return self._keep_running(mcp_server, origin, str(e))
        except DiscoveryServerGone as e:
            return self._refuse_once(mcp_server, origin, str(e))
        logger.info("discovered_mcp_server_taken_back_changed", mcp_server_id=mcp_server.name)
        return True

    def _keep_running(self, mcp_server: DiscoveredMcpServer, origin: DiscoveryOrigin, detail: str) -> bool:
        """Refuse the report, and track the server as the definition it runs.

        Its source then still governs it: it goes once no longer reported, and
        the next report is a change to it.
        """
        ttl = self._orchestrator.config.default_ttl_s
        running = _restored_one(mcp_server.name, self._repository.get(mcp_server.name), ttl)
        if running is not None:
            self._orchestrator.track_restored(running)
        return self._refuse_once(mcp_server, origin, detail)

    def _refuse_once(self, mcp_server: DiscoveredMcpServer, origin: DiscoveryOrigin, detail: str) -> bool:
        # Repeated every cycle the source reports the name; said once.
        if (mcp_server.name, origin.source_type) not in self._refused:
            self._refused.add((mcp_server.name, origin.source_type))
            logger.warning(
                "discovery_takeover_refused",
                mcp_server_name=mcp_server.name,
                source_type=origin.source_type,
                detail=detail,
            )
        return False
