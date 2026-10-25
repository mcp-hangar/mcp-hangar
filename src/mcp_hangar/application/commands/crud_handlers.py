"""CQRS command handlers for McpServer and Group CRUD operations.

All handlers:
- Receive dependencies via constructor (Dependency Inversion Principle)
- Emit domain events via the event bus after successful mutations
- Raise domain exceptions on invalid operations (not found, duplicate, etc.)
- Are thread-safe (mutations guarded via repository or injected lock)
"""

import dataclasses
import threading
from collections.abc import Callable
from typing import Any, cast

from ...domain.contracts.command import CommandHandler
from ...domain.contracts.fleet import IFleetWriter
from ...domain.contracts.persistence import ConcurrentModificationError, PeerKeyClaim, PeerKeyNameRefused
from ...domain.events import (
    EgressPolicyCleared,
    EgressPolicySet,
    McpServerDeregistered,
    McpServerRegistered,
)
from ...domain.exceptions import (
    McpServerDeclaredInConfigError,
    McpServerNotFoundError,
    PeerIdentityChangeDenied,
    PeerKeyNameTaken,
    ValidationError,
)
from ...domain.model.mcp_server import McpServer
from ...domain.model.mcp_server_group import GroupDeleted, McpServerGroup
from ...domain.repository import IMcpServerRepository
from ...domain.security.ssrf import validate_no_ssrf
from ...domain.services.fleet_snapshot import snapshot_of
from ...domain.value_objects import HealthCheckInterval, IdleTTL, LoadBalancerStrategy, McpServerMode, McpServerState
from ...domain.value_objects.peer_identity import PeerIdentity, max_connection_age
from ...domain.value_objects.provenance import Provenance
from ...logging_config import get_logger
from ...stream_ids import MCP_SERVER
from ..group_events import publish_group_events
from ..ports.log_buffers import ILogBuffers
from ..ports.peer_identity import IPeerIdentityAuthority
from .crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    CreateMcpServerCommand,
    DeleteGroupCommand,
    DeleteMcpServerCommand,
    ForgetPeerKeyNameCommand,
    RemoveGroupMemberCommand,
    SetL7PolicyCommand,
    UpdateGroupCommand,
    UpdateMcpServerCommand,
)

logger = get_logger(__name__)

#: How long replacing a discovered server waits for its calls in flight: the
#: bound a graceful reload uses for the servers it restarts (#2091).
REPLACE_DRAIN_TIMEOUT_S = 30.0


def _require_peer_identity_authority(
    authority: IPeerIdentityAuthority | None,
    command: CreateMcpServerCommand | UpdateMcpServerCommand | DeleteMcpServerCommand | ForgetPeerKeyNameCommand,
    what: str,
) -> None:
    """Refuse a change to a peer identity the command's caller may not make (ADR-030 §9).

    A discovery source never may, in any authentication mode: it reports what
    runs, and a key list is a declaration about which upstream may answer. Any
    other caller needs `mcp_servers:peer_identity`, decided by *authority*; a
    handler wired without one refuses, so a missing wire fails closed.
    """
    principal = command.principal
    who = str(principal.id) if principal is not None else "(no principal)"
    if command.provenance is Provenance.DISCOVERY:
        raise PeerIdentityChangeDenied(
            who, what, command.mcp_server_id, "a discovery source does not declare peer identities"
        )
    if authority is None or not authority.may_declare(principal):
        raise PeerIdentityChangeDenied(who, what, command.mcp_server_id, "requires mcp_servers:peer_identity")


#: What a `tls` block may hold when it comes through a command rather than the
#: configuration file. `verify_ssl` and `ca_cert_path` decide whose certificate
#: an upstream may present; they stay with whoever writes the file (ADR-021).
_COMMAND_TLS_KEYS = frozenset({"peer_identity", "max_connection_age"})


def _command_peer_identity(mcp_server_id: str, tls: Any) -> PeerIdentity | None:
    """Check a `tls` block a command carries, and return the peer identity it declares (ADR-030 §4b)."""
    if tls is None:
        return None
    if not isinstance(tls, dict):
        raise ValidationError(f"mcp_server '{mcp_server_id}': tls must be a mapping")
    unknown = sorted(set(tls) - _COMMAND_TLS_KEYS)
    if unknown:
        raise ValidationError(
            f"mcp_server '{mcp_server_id}': tls accepts only peer_identity and max_connection_age here; "
            f"{', '.join(unknown)} can be set only in the configuration file"
        )
    try:
        identity = PeerIdentity.from_tls(tls)
        max_connection_age(tls)
    except ValueError as e:
        raise ValidationError(f"mcp_server '{mcp_server_id}': {e}") from e
    return identity


def _refuse_a_key_ref_the_fleet_holds(repository: IMcpServerRepository, mcp_server_id: str, key_ref: str) -> None:
    """Refuse a `key_ref` another server in the fleet holds (ADR-030 §7).

    The record's entries from the configuration file are its servers, which
    never write a row, so the record's own write cannot see them.
    """
    for name, server in repository.get_all().items():
        held = getattr(server, "_peer_identity", None)
        if name != mcp_server_id and isinstance(held, PeerIdentity) and held.key_ref == key_ref:
            raise PeerKeyNameTaken(
                f"mcp_server '{mcp_server_id}': key_ref '{key_ref}' already names the server '{name}'"
            )


def _refuse_an_endpoint_no_launcher_can_dial(mcp_server_id: str, endpoint: str) -> None:
    """Refuse a remote endpoint the HTTP launcher would refuse at start (#2056).

    The rule is the launcher's (`HttpLauncher._validate_endpoint`): http or https,
    with a host. Checked only there, a `file://` endpoint was accepted over REST,
    stored and listed, and failed at every start.
    """
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError(
            f"mcp_server '{mcp_server_id}': endpoint must be an http or https URL with a host, got {endpoint!r}"
        )


def _refuse_plaintext_for_a_peer_identity(mcp_server_id: str, endpoint: str | None) -> None:
    """A key is checked in a TLS handshake, so a key-pinned server is reached over https (ADR-030 §4).

    The transport refuses to connect otherwise; refused here, the mistake is
    the caller's 422 rather than a server that never starts.
    """
    if endpoint is not None and not endpoint.lower().startswith("https://"):
        raise ValidationError(f"mcp_server '{mcp_server_id}': a server with tls.peer_identity needs an https endpoint")


def _record_fields(
    fleet_writer: IFleetWriter, mcp_server: McpServer, peer_key: PeerKeyClaim = PeerKeyClaim.CLAIM, **fields: Any
) -> None:
    """Record a change to some fields of a server, on top of the recorded row (#1954).

    The row is the base, not this replica's copy: a copy that has not yet seen a
    peer's change would otherwise write every other field back as it was -- an
    older L7 policy included -- and win. The write is refused if the row changed
    after it was read. A server with no row (one from the configuration file)
    is recorded from this replica's copy, and only if no row appeared meanwhile.
    """
    recorded = fleet_writer.read(mcp_server.mcp_server_id)
    if recorded is None:
        # A server from the configuration file: its peer-key entries are the
        # file's, derived at load. A row written for it claims none.
        fleet_writer.save_versioned(dataclasses.replace(snapshot_of(mcp_server), **fields), 0, PeerKeyClaim.SKIP)
        return
    base, version = recorded
    fleet_writer.save_versioned(dataclasses.replace(base, **fields), version, peer_key)


def _get_mcp_server_or_raise(repository: IMcpServerRepository, mcp_server_id: str) -> McpServer:
    """Fetch a server by id, or raise `McpServerNotFoundError`.

    Deliberately NOT the same helper as `BaseMcpServerHandler._get_mcp_server`
    in `handlers.py`: that one also consults the runtime store. Update and
    Delete only touch the repository, and each carried its own byte-identical
    copy of this.
    """
    mcp_server = repository.get(mcp_server_id)
    if mcp_server is None:
        raise McpServerNotFoundError(mcp_server_id)
    return cast(McpServer, mcp_server)


# =============================================================================
# McpServer CRUD Handlers
# =============================================================================


class CreateMcpServerHandler(CommandHandler):
    """Handler for CreateMcpServerCommand.

    Creates a new McpServer in the repository and emits McpServerRegistered.
    Raises ValidationError if a mcp_server with the same ID already exists.
    """

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: Any,
        fleet_writer: IFleetWriter | None = None,
        coordinated: Callable[[], bool] | None = None,
        log_buffers: ILogBuffers | None = None,
        peer_identity_authority: IPeerIdentityAuthority | None = None,
        drain: Callable[[str, float], bool] | None = None,
        groups: dict | None = None,
    ) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository for persistence.
            event_bus: Event bus for publishing domain events.
            fleet_writer: Where the configuration is recorded so a restart can
                rebuild it. None keeps the previous behaviour -- in memory only,
                gone on restart -- which is what a configuration with no storage
                backend gets.
            coordinated: Whether this gateway shares its state with peers. When
                it does, `subprocess` and `docker` are refused: they run a child
                process of one gateway, so every replica that learned about the
                server would start its own copy. Absent means standalone, where
                every mode is available and always was.
            log_buffers: Where a created server gets the log buffer its output
                is read into (#1747), as a reloaded (#1502) and a hot-loaded
                (#1506) one do. None means no buffer, as before.
            drain: Waits, for at most the given seconds, until a server has no
                call in flight; what a graceful reload waits on. Used before a
                discovered server is replaced (#2091). None stops it at once.
            groups: The groups, whose members hold a server by reference and
                follow a replaced discovered server to its new copy.
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer
        self._coordinated = coordinated or (lambda: False)
        self._log_buffers = log_buffers
        self._peer_identity_authority = peer_identity_authority
        self._drain = drain
        self._groups = groups

    def handle(self, command: CreateMcpServerCommand) -> dict[str, Any]:
        """Create a new mcp_server.

        Args:
            command: CreateMcpServerCommand with mcp_server configuration.

        Returns:
            Dict with mcp_server_id and created flag.

        Raises:
            ValidationError: If a mcp_server with the same ID already exists.
        """
        replaced = self._discovered_server_to_replace(command)

        self._refuse_local_mode_when_coordinating(command.mode)

        # A key list decides which upstream a call may reach: declaring one is
        # its own permission, checked before anything is built (ADR-030 §9).
        peer_identity = _command_peer_identity(command.mcp_server_id, command.tls)
        if peer_identity is not None:
            _require_peer_identity_authority(self._peer_identity_authority, command, "declare a peer identity for")
            # The peer-key name record keeps a deleted key-pinned server from
            # coming back as a plain one. Without durable storage there is no
            # record, so the declaration is refused rather than served unguarded.
            if self._fleet_writer is None:
                raise ValidationError(
                    f"mcp_server '{command.mcp_server_id}': declaring a peer identity needs a durable "
                    "configuration store (persistence.backend sqlite or postgresql); this gateway keeps none"
                )
            _refuse_a_key_ref_the_fleet_holds(self._repository, command.mcp_server_id, peer_identity.key_ref)
            _refuse_plaintext_for_a_peer_identity(command.mcp_server_id, command.endpoint)

        if McpServerMode.normalize(command.mode) == McpServerMode.REMOTE and command.endpoint is not None:
            _refuse_an_endpoint_no_launcher_can_dial(command.mcp_server_id, command.endpoint)
            validate_no_ssrf(
                command.endpoint,
                provenance=command.provenance,
                runtime_addresses=command.runtime_addresses,
            )

        mcp_server = McpServer(
            mcp_server_id=command.mcp_server_id,
            mode=command.mode,
            command=command.command,
            image=command.image,
            endpoint=command.endpoint,
            env=dict(command.env) if command.env else None,
            idle_ttl_s=command.idle_ttl_s,
            health_check_interval_s=command.health_check_interval_s,
            description=command.description,
            volumes=command.volumes,
            read_only=command.read_only,
            tls=command.tls,
            # The same policy inputs `validate_no_ssrf` used just above, carried
            # onto the aggregate so the transport's connect-time re-check (DNS
            # rebinding) applies them too. `validate_no_ssrf` guards registration;
            # the client that connects is built later, on first use. The
            # connect-time guard is turned on for exactly what was validated
            # above -- a registered remote endpoint -- and stays off for servers
            # that never reach this handler (config-file / directly built).
            provenance=command.provenance,
            runtime_addresses=command.runtime_addresses,
            enforce_ssrf=(
                McpServerMode.normalize(command.mode) == McpServerMode.REMOTE and command.endpoint is not None
            ),
            # Kept by the aggregate for a DISCOVERY server only.
            discovered_as=command.discovered_as,
        )

        # Only now, with every check above passed and the new server built: a
        # refused definition leaves the running one exactly as it was. Its L7
        # egress policy is carried over, as a reload carries it: a changed
        # definition must not lift enforcement until the operator pushes again.
        # The record is swapped first; the running server is retired only
        # once that write has gone through.
        if replaced is not None:
            if replaced._l7_policy is not None:
                mcp_server.set_l7_policy(replaced._l7_policy)
            self._swap_record(mcp_server, command)
            self._retire(replaced, mcp_server, command)

        # Recorded before it joins the fleet, and before the event. The order is
        # the point: a write that fails leaves nothing behind and the caller is
        # told the registration did not happen. The other order gives a running
        # server, a published event, and no record -- which is what a restart
        # then quietly resolves by forgetting the server.
        # Insert-only: another replica may have created it already, before this
        # one heard of it (#1954). Its row is refused rather than overwritten.
        elif self._fleet_writer is not None:
            try:
                self._fleet_writer.save_versioned(snapshot_of(mcp_server), None)
            except ConcurrentModificationError as e:
                raise ValidationError(f"McpServer already exists: {command.mcp_server_id}") from e
            except PeerKeyNameRefused as e:
                raise PeerKeyNameTaken(str(e)) from e

        # Before it joins the repository, so nothing can start it -- and write
        # output -- with no buffer attached.
        if self._log_buffers is not None and self._log_buffers.attach(command.mcp_server_id, mcp_server):
            logger.info("log_buffer_attached_to_mcp_server", mcp_server_id=command.mcp_server_id)
        self._repository.add(command.mcp_server_id, mcp_server)

        self._event_bus.publish(
            McpServerRegistered(
                mcp_server_id=command.mcp_server_id,
                source=command.source,
                mode=command.mode,
            )
        )

        logger.info(
            "mcp_server_created",
            mcp_server_id=command.mcp_server_id,
            mode=command.mode,
            source=command.source,
        )
        return {"mcp_server_id": command.mcp_server_id, "created": True}

    def _discovered_server_to_replace(self, command: CreateMcpServerCommand) -> McpServer | None:
        """The server this command replaces, None when the id is free; refused otherwise.

        Only discovery replaces, and only what discovery registered (#2091): an
        edited definition file or changed labels. A server an operator created,
        re-pointed over the API (which makes it HUMAN), or the configuration
        file declares is never replaced, so a discovered definition cannot take
        over a name someone else owns.
        """
        existing = self._repository.get(command.mcp_server_id)
        if existing is None:
            return None
        if (
            command.replace_discovered
            and command.provenance is Provenance.DISCOVERY
            and getattr(existing, "provenance", None) is Provenance.DISCOVERY
        ):
            return cast(McpServer, existing)
        raise ValidationError(f"McpServer already exists: {command.mcp_server_id}")

    def _swap_record(self, new: McpServer, command: CreateMcpServerCommand) -> None:
        """Wait for the calls in flight, then overwrite the server's row in one versioned write.

        Calls in flight are waited for first, as a graceful reload waits for a
        server it restarts. The row is replaced in place, never deleted and
        inserted: a write that does not apply -- a peer changed the row since it
        was read (`ConcurrentModificationError`) or the peer-key record refuses
        it -- raises before anything else has changed, and the running server
        keeps running everywhere.
        """
        name = command.mcp_server_id
        if self._drain is not None and not self._drain(name, REPLACE_DRAIN_TIMEOUT_S):
            logger.warning("mcp_server_replace_drain_timed_out", mcp_server_id=name, timeout_s=REPLACE_DRAIN_TIMEOUT_S)
        if self._fleet_writer is None:
            return
        recorded = self._fleet_writer.read(name)
        try:
            self._fleet_writer.save_versioned(snapshot_of(new), 0 if recorded is None else recorded[1])
        except PeerKeyNameRefused as e:
            raise PeerKeyNameTaken(str(e)) from e

    def _retire(self, old: McpServer, new: McpServer, command: CreateMcpServerCommand) -> None:
        """Take the replaced server out of this replica's fleet, after its row was swapped.

        Peers see a deregistration and then a registration: their projection
        ignores a registration of a name they already hold, and reads the new
        row on the second. A group member is pointed at the new copy, so the
        group does not route to (and relaunch) the stopped one.
        """
        name = command.mcp_server_id
        if old.state is not McpServerState.COLD:
            old.shutdown()
            self._event_bus.publish_aggregate_events(MCP_SERVER, name, old.collect_events())
        self._repository.remove(name)
        if self._log_buffers is not None:
            self._log_buffers.release(name)
        for group in (self._groups or {}).values():
            group.replace_member_server(name, new)
        self._event_bus.publish(McpServerDeregistered(mcp_server_id=name, source=command.source))
        logger.info("mcp_server_replaced", mcp_server_id=name, source=command.source)

    def _refuse_local_mode_when_coordinating(self, mode: str) -> None:
        """Refuse a mode that runs a child process, when there are peers.

        At registration, which is where the mistake is made and where an
        operator can still act on it. The launcher refuses it too (#790, phase
        4.1), but by then the server is in the fleet and in the shared record,
        and the failure has moved to whoever calls it.

        `subprocess` and `docker` attach a child process's stdio to *one*
        gateway. There is no address a peer could use, so a replica that learns
        of such a server and serves a call to it starts its own copy -- a second
        server, with its own mounted volumes, not a second route to the first.
        """
        if not self._coordinated():
            return
        if McpServerMode.normalize(mode) not in (
            McpServerMode.SUBPROCESS,
            McpServerMode.DOCKER,
            McpServerMode.CONTAINER,
        ):
            return
        raise ValidationError(
            f"mode '{mode}' runs the server as a child process of one gateway, and this deployment keeps its "
            "state in storage that peers can share. Every replica that learned about this server would start "
            "its own copy, with its own volumes. Use 'remote' mode -- or, if this really is one gateway and "
            "always will be, storage that cannot be shared (`persistence.backend: sqlite`)."
        )


class ForgetPeerKeyNameHandler(CommandHandler):
    """Removes a deleted key-pinned server's name from the peer-key name record (ADR-030 §9)."""

    def __init__(
        self,
        repository: IMcpServerRepository,
        fleet_writer: IFleetWriter | None = None,
        peer_identity_authority: IPeerIdentityAuthority | None = None,
    ) -> None:
        self._repository = repository
        self._fleet_writer = fleet_writer
        self._peer_identity_authority = peer_identity_authority

    def handle(self, command: ForgetPeerKeyNameCommand) -> dict[str, Any]:
        """Forget the name, which only a holder of `mcp_servers:peer_identity` may do.

        Raises:
            PeerKeyNameTaken: The server is still registered under the name.
            McpServerNotFoundError: The record holds no entry for it.
        """
        name = command.mcp_server_id
        _require_peer_identity_authority(self._peer_identity_authority, command, "forget the peer-key name")
        if self._repository.exists(name):
            raise PeerKeyNameTaken(f"mcp_server '{name}': delete the server before forgetting its peer-key name")
        if self._fleet_writer is None:
            raise McpServerNotFoundError(name)
        try:
            forgotten = self._fleet_writer.forget_peer_key_name(name)
        except PeerKeyNameRefused as e:
            raise PeerKeyNameTaken(str(e)) from e
        if not forgotten:
            raise McpServerNotFoundError(name)
        logger.info("peer_key_name_forgotten", mcp_server_id=name, source=command.source)
        return {"mcp_server_id": name, "forgotten": True}


class UpdateMcpServerHandler(CommandHandler):
    """Handler for UpdateMcpServerCommand.

    Updates mutable configuration fields on an existing McpServer via
    McpServer.update_config(). Raises McpServerNotFoundError if not found.
    """

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: Any,
        fleet_writer: IFleetWriter | None = None,
        declared_in_config: Callable[[str], bool] | None = None,
        peer_identity_authority: IPeerIdentityAuthority | None = None,
        groups: dict | None = None,
    ) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository for persistence.
            event_bus: Event bus for publishing domain events.
            fleet_writer: Where the changed configuration is recorded. Without
                it an update survives only until the process ends, and a restart
                silently reverts it.
            declared_in_config: Whether the configuration declares a server,
                whose endpoint and `tls` the file owns.
            peer_identity_authority: Who may change a peer identity, or the
                endpoint of a key-pinned server (ADR-030 §9).
            groups: The groups, whose members hold a server by reference.
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer
        self._declared_in_config = declared_in_config
        self._peer_identity_authority = peer_identity_authority
        self._groups = groups

    def handle(self, command: UpdateMcpServerCommand) -> dict[str, Any]:
        """Update mcp_server configuration.

        Delegates field updates to McpServer.update_config() which acquires the
        mcp_server lock internally and records a McpServerUpdated event on the aggregate.

        Args:
            command: UpdateMcpServerCommand with fields to update.

        Returns:
            Dict with mcp_server_id and updated flag.

        Raises:
            McpServerNotFoundError: If mcp_server does not exist.
        """
        mcp_server = _get_mcp_server_or_raise(self._repository, command.mcp_server_id)

        # Recorded first, and only the fields this command changes (#1954). An
        # update that is not recorded is reverted by the next restart; one that
        # is recorded from this replica's whole copy reverts whatever a peer
        # changed meanwhile. Validated before the write, so a bad value cannot
        # leave the record changed and the server not.
        if command.idle_ttl_s is not None:
            IdleTTL(command.idle_ttl_s)
        if command.health_check_interval_s is not None:
            HealthCheckInterval(command.health_check_interval_s)
        connection = self._connection_change(mcp_server, command)
        if self._fleet_writer is not None:
            changed: dict[str, Any] = {
                "description": command.description,
                "env": dict(command.env) if command.env is not None else None,
                "idle_ttl_s": command.idle_ttl_s,
                "health_check_interval_s": command.health_check_interval_s,
            }
            fields = {k: v for k, v in changed.items() if v is not None}
            if connection is not None:
                endpoint, tls = connection
                fields.update(
                    endpoint=endpoint,
                    tls=tls,
                    provenance=Provenance.HUMAN,
                    runtime_addresses=None,
                    enforce_ssrf=endpoint is not None,
                )
            try:
                _record_fields(self._fleet_writer, mcp_server, self._peer_key_claim(mcp_server, connection), **fields)
            except PeerKeyNameRefused as e:
                raise PeerKeyNameTaken(str(e)) from e

        if connection is not None:
            mcp_server.reconfigure_connection(*connection)
        mcp_server.update_config(
            description=command.description,
            env=command.env,
            idle_ttl_s=command.idle_ttl_s,
            health_check_interval_s=command.health_check_interval_s,
        )

        # Collect the McpServerUpdated event recorded by update_config() and
        # append it to the aggregate's stream, which also delivers it.
        self._event_bus.publish_aggregate_events(MCP_SERVER, mcp_server.mcp_server_id, mcp_server.collect_events())

        logger.info(
            "mcp_server_updated",
            mcp_server_id=command.mcp_server_id,
            source=command.source,
        )
        return {"mcp_server_id": command.mcp_server_id, "updated": True}

    @staticmethod
    def _peer_key_claim(
        mcp_server: McpServer, connection: tuple[str | None, dict[str, Any] | None] | None
    ) -> PeerKeyClaim:
        """MOVE when the update gives a key-pinned server another `key_ref` (§7), CLAIM otherwise."""
        old = mcp_server._peer_identity
        new = PeerIdentity.from_tls(connection[1]) if connection is not None else old
        return (
            PeerKeyClaim.MOVE
            if old is not None and new is not None and new.key_ref != old.key_ref
            else PeerKeyClaim.CLAIM
        )

    def _connection_change(
        self, mcp_server: McpServer, command: UpdateMcpServerCommand
    ) -> tuple[str | None, dict[str, Any] | None] | None:
        """Check a change of endpoint or `tls`, and return the server's new pair (ADR-030 §4b, §9).

        Any server's endpoint may change where the fleet projection applies it;
        a key-pinned server's, and any peer identity, only for a holder of
        `mcp_servers:peer_identity`. A server the configuration
        declares keeps the file's. One update may rotate keys but not replace
        the whole list under the same `key_ref`, so a single write cannot cut
        every trusted key at once; a new `key_ref` starts a new lineage.
        """
        if command.endpoint is None and command.tls is None:
            return None
        name = command.mcp_server_id
        if self._declared_in_config is not None and self._declared_in_config(name):
            raise McpServerDeclaredInConfigError(name)
        if mcp_server.mode != McpServerMode.REMOTE:
            raise ValidationError(f"mcp_server '{name}': only a remote server has an endpoint and a tls block")
        endpoint = mcp_server._endpoint if command.endpoint is None else (command.endpoint or None)
        tls = mcp_server._tls_config if command.tls is None else command.tls
        new = _command_peer_identity(name, command.tls) if command.tls is not None else mcp_server._peer_identity
        old = mcp_server._peer_identity
        if self._fleet_writer is None:
            # The fleet projection replaces a copy whose connection changed, on
            # every replica and this one; without durable storage none runs,
            # and the live client would keep the old endpoint (#1955).
            raise ValidationError(
                f"mcp_server '{name}': changing an endpoint or tls block needs a durable configuration store"
            )
        if old is None and new is None and endpoint is None:
            raise ValidationError(f"mcp_server '{name}': a remote server without a peer identity needs an endpoint")
        if old is not None or new is not None:
            _require_peer_identity_authority(self._peer_identity_authority, command, "change the peer identity of")
        if new is not None and (old is None or new.key_ref != old.key_ref):
            _refuse_a_key_ref_the_fleet_holds(self._repository, name, new.key_ref)
        if old is not None and new is None:
            raise PeerKeyNameTaken(f"mcp_server '{name}': a peer identity cannot be removed; the name keeps it")
        if old is not None and new is not None and new.key_ref == old.key_ref:
            # A new key_ref is a new lineage, whose list need share nothing.
            if not new.spki_sha256 & old.spki_sha256:
                raise ValidationError(
                    f"mcp_server '{name}': the new key list shares no key with the current one; "
                    "add the next key first, then remove the old one"
                )
        if new is not None and any(g.get_member(name) for g in (self._groups or {}).values()):
            raise ValidationError(f"mcp_server '{name}': a group member cannot be given a peer identity")
        if new is not None:
            _refuse_plaintext_for_a_peer_identity(name, endpoint)
        if endpoint is not None:
            _refuse_an_endpoint_no_launcher_can_dial(name, endpoint)
            # Set over the API, the endpoint is a human's, whatever it was
            # before: a server moved from discovery meets the policy now (§4f).
            validate_no_ssrf(endpoint, provenance=Provenance.HUMAN)
        return endpoint, tls


class SetL7PolicyHandler(CommandHandler):
    """Handler for SetL7PolicyCommand.

    Attaches, replaces, or clears the L7 egress policy on an existing McpServer
    via McpServer.set_l7_policy(). Raises McpServerNotFoundError if not found.
    """

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: Any,
        fleet_writer: IFleetWriter | None = None,
        restore_gap: str | None = None,
    ) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository the policy is attached in.
            event_bus: Event bus for publishing domain events.
            fleet_writer: Where the policy is recorded. None keeps it in memory
                only, gone on restart.
            restore_gap: Why the next start will not read the record back, or
                None when it will. With *fleet_writer* it decides what the push
                reports as ``persisted`` (#1306).
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer
        self._not_persisted_reason = "no_durable_backend" if fleet_writer is None else restore_gap

    def handle(self, command: SetL7PolicyCommand) -> dict[str, Any]:
        """Set (or clear) the mcp_server's L7 policy.

        Raises:
            McpServerNotFoundError: If mcp_server does not exist.
        """
        mcp_server = self._repository.get(command.mcp_server_id)
        if mcp_server is None:
            raise McpServerNotFoundError(command.mcp_server_id)

        # Record it, like every other configuration change (#991), and before
        # the server takes it. Unrecorded, the policy lived only in this
        # replica's RAM: a restart dropped it and peer replicas never had it, so
        # denied tools ran while the CR reported enforcement. The save also gives
        # the peer projection a row to read. Only the policy is written, on top
        # of the recorded row, so a replica that has not yet seen a peer's change
        # does not write the rest of its stale copy back (#1954).
        if self._fleet_writer is not None:
            _record_fields(
                self._fleet_writer,
                cast(McpServer, mcp_server),
                l7_policy=command.policy.to_wire() if command.policy is not None else None,
            )

        cast(McpServer, mcp_server).set_l7_policy(command.policy)

        # Changing this policy changes what the enforcement plane blocks, so
        # the change belongs in the audit trail and not only in a log line.
        # Every sibling handler in this module publishes; this one took an
        # event_bus and never used it.
        if command.policy is None:
            self._event_bus.publish(EgressPolicyCleared(mcp_server_id=command.mcp_server_id, source=command.source))
        else:
            policy = command.policy
            self._event_bus.publish(
                EgressPolicySet(
                    mcp_server_id=command.mcp_server_id,
                    source=command.source,
                    mode=policy.mode.value,
                    default_action=policy.default_action.value,
                    allow_rules=len(policy.tools.allow),
                    deny_rules=len(policy.tools.deny),
                    require_approval_rules=len(policy.tools.require_approval),
                    secret_pattern_groups=list(policy.arguments.secret_patterns),
                    max_payload_bytes=policy.arguments.max_payload_bytes,
                    policy_id=policy.policy_id,
                )
            )

        logger.info(
            "mcp_server_l7_policy_set",
            mcp_server_id=command.mcp_server_id,
            cleared=command.policy is None,
            source=command.source,
            policy_id=None if command.policy is None else command.policy.policy_id,
        )
        # Whether a restart of this gateway gives the policy back. The operator
        # only re-delivers on its next reconcile, so a policy that is not kept
        # leaves every restart ungoverned until then, and nothing else says so:
        # the CR keeps reporting it (#1306). A clear always is: a gateway that
        # keeps nothing starts with no policy, which is what was asked for.
        persisted = command.policy is None or self._not_persisted_reason is None
        if not persisted and command.policy is not None:
            logger.warning(
                "l7_policy_not_persisted",
                mcp_server_id=command.mcp_server_id,
                reason=self._not_persisted_reason,
                policy_id=command.policy.policy_id,
                detail="a gateway restart drops this policy until the operator delivers it again",
            )
        return {
            "mcp_server_id": command.mcp_server_id,
            "l7_policy_set": command.policy is not None,
            "persisted": persisted,
        }


class DeleteMcpServerHandler(CommandHandler):
    """Handler for DeleteMcpServerCommand.

    Stops a mcp_server that is not COLD, then removes it from the repository
    and emits McpServerDeregistered.
    Raises McpServerNotFoundError if the mcp_server does not exist.
    """

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: Any,
        fleet_writer: IFleetWriter | None = None,
        log_buffers: ILogBuffers | None = None,
        declared_in_config: Callable[[str], bool] | None = None,
        peer_identity_authority: IPeerIdentityAuthority | None = None,
    ) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository for persistence.
            event_bus: Event bus for publishing domain events.
            fleet_writer: Where the removal is recorded. A row left behind
                resurrects the server on the next restart.
            log_buffers: Where the deleted server's log buffer is released.
                Omitted means none is, and its output stays registered under an
                id that is free again -- what deleting did before #1506.
            declared_in_config: Whether the gateway configuration declares a
                server; such a server is refused (#1744). Omitted means none is.
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer
        self._log_buffers = log_buffers
        self._declared_in_config = declared_in_config
        self._peer_identity_authority = peer_identity_authority

    def handle(self, command: DeleteMcpServerCommand) -> dict[str, Any]:
        """Delete a mcp_server, stopping it first if running.

        Args:
            command: DeleteMcpServerCommand with mcp_server_id to delete.

        Returns:
            Dict with mcp_server_id and deleted flag.

        Raises:
            McpServerNotFoundError: If mcp_server does not exist.
            McpServerDeclaredInConfigError: If the configuration declares it.
        """
        mcp_server = _get_mcp_server_or_raise(self._repository, command.mcp_server_id)

        # Before anything is stopped: the next reload or restart would bring a
        # declared server back, so the delete would only look like it worked
        # (#1744). Whoever asked -- the API, the Kubernetes operator, a
        # discovery source whose name collides with a declared one -- is told.
        if self._declared_in_config is not None and self._declared_in_config(command.mcp_server_id):
            raise McpServerDeclaredInConfigError(command.mcp_server_id)

        # Removing a key-pinned server is a change to its peer identity: without
        # this, a delete and a plain re-create under the same name would
        # downgrade it with `lifecycle` and `write` alone (ADR-030 §9).
        if isinstance(getattr(mcp_server, "_peer_identity", None), PeerIdentity):
            _require_peer_identity_authority(self._peer_identity_authority, command, "remove the peer identity of")

        # Stop mcp_server unless it is COLD. A DEAD one too: a crashed process
        # may still hold its connection (#1361). I/O (shutdown) is done outside
        # the repository lock to respect the no-I/O-under-lock rule.
        if mcp_server.state is not McpServerState.COLD:
            mcp_server.shutdown()
            # Persist and publish any lifecycle events emitted by shutdown()
            self._event_bus.publish_aggregate_events(MCP_SERVER, mcp_server.mcp_server_id, mcp_server.collect_events())

        # Before it leaves the fleet, for the same reason registration records
        # before joining it: a failure here must leave the server as it was,
        # rather than removed from memory and still on record.
        #
        # A convergence loop's deletion is fenced; an operator's is not. See
        # `IFleetWriter.delete` for the sequence that distinction exists for --
        # briefly, a stalled leader whose decision is about a fleet that has
        # since changed hands, and which cannot notice on its own because it was
        # frozen at the moment it would have had to.
        if self._fleet_writer is not None:
            self._fleet_writer.delete(
                command.mcp_server_id,
                fenced=command.provenance is Provenance.DISCOVERY,
            )

        self._repository.remove(command.mcp_server_id)

        # The buffer registry is a process-wide dict that the removal above does
        # not reach, so a deleted server's output stayed registered under an id
        # that is free again (#1506) -- the leak a reload's removal had until
        # #1502. Through the port: a command handler may not reach the registry,
        # which sits a layer above it.
        if self._log_buffers is not None:
            self._log_buffers.release(command.mcp_server_id)
            logger.info("log_buffer_released", mcp_server_id=command.mcp_server_id)

        # After its last lifecycle events, above. The metrics handler drops the
        # server's gauges on this one (#1361).
        self._event_bus.publish(
            McpServerDeregistered(
                mcp_server_id=command.mcp_server_id,
                source=command.source,
            )
        )

        logger.info(
            "mcp_server_deleted",
            mcp_server_id=command.mcp_server_id,
            source=command.source,
        )
        return {"mcp_server_id": command.mcp_server_id, "deleted": True}


# =============================================================================
# Group CRUD Handlers
# =============================================================================


class CreateGroupHandler(CommandHandler):
    """Handler for CreateGroupCommand.

    Creates a new McpServerGroup in the groups dict and emits GroupCreated.
    Raises ValidationError if a group with the same ID already exists.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to McpServerGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: CreateGroupCommand) -> dict[str, Any]:
        """Create a new mcp_server group.

        Args:
            command: CreateGroupCommand with group configuration.

        Returns:
            Dict with group_id and created flag.

        Raises:
            ValidationError: If a group with the same ID already exists.
        """
        with self._lock:
            if command.group_id in self._groups:
                raise ValidationError(f"Group already exists: {command.group_id}")
            group = McpServerGroup(
                group_id=command.group_id,
                strategy=LoadBalancerStrategy(command.strategy),
                min_healthy=command.min_healthy,
                description=command.description,
            )
            self._groups[command.group_id] = group

        # Publish events OUTSIDE lock (no I/O under lock)
        publish_group_events(self._event_bus, group)

        logger.info("group_created", group_id=command.group_id, strategy=command.strategy)
        return {"group_id": command.group_id, "created": True}


class UpdateGroupHandler(CommandHandler):
    """Handler for UpdateGroupCommand.

    Updates mutable configuration fields on an existing McpServerGroup via
    McpServerGroup.update(). Raises McpServerNotFoundError if not found.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to McpServerGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: UpdateGroupCommand) -> dict[str, Any]:
        """Update group configuration.

        Delegates field updates to McpServerGroup.update() which acquires the
        group lock internally and records a GroupUpdated event on the aggregate.

        Args:
            command: UpdateGroupCommand with fields to update.

        Returns:
            Dict with group_id and updated flag.

        Raises:
            McpServerNotFoundError: If group does not exist.
        """
        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise McpServerNotFoundError(command.group_id)

        # group.update() acquires its own lock internally
        group.update(
            strategy=command.strategy,
            description=command.description,
            min_healthy=command.min_healthy,
        )

        # Collect the GroupUpdated event and forward through event bus
        publish_group_events(self._event_bus, group)

        logger.info("group_updated", group_id=command.group_id, source=command.source)
        return {"group_id": command.group_id, "updated": True}


class DeleteGroupHandler(CommandHandler):
    """Handler for DeleteGroupCommand.

    Removes a McpServerGroup from the groups dict, calls stop_all() on the
    group (outside the lock), and emits GroupDeleted.
    Raises McpServerNotFoundError if not found.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to McpServerGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: DeleteGroupCommand) -> dict[str, Any]:
        """Delete a mcp_server group, stopping all members first.

        Args:
            command: DeleteGroupCommand with group_id to delete.

        Returns:
            Dict with group_id and deleted flag.

        Raises:
            McpServerNotFoundError: If group does not exist.
        """
        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise McpServerNotFoundError(command.group_id)
            del self._groups[command.group_id]

        # I/O (stop) outside lock — stop_all() acquires McpServer locks individually
        group.stop_all()

        # Collect any lifecycle events from stop_all(), then emit GroupDeleted
        publish_group_events(self._event_bus, group)
        self._event_bus.publish(GroupDeleted(group_id=command.group_id))

        logger.info("group_deleted", group_id=command.group_id, source=command.source)
        return {"group_id": command.group_id, "deleted": True}


class AddGroupMemberHandler(CommandHandler):
    """Handler for AddGroupMemberCommand.

    Adds a McpServer to an existing McpServerGroup.
    Raises McpServerNotFoundError if mcp_server or group does not exist.
    Thread-safe via a per-handler threading.Lock for the groups dict lookup.
    """

    def __init__(self, repository: IMcpServerRepository, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository for member lookup.
            groups: Shared groups dict mapping group_id to McpServerGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._repository = repository
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: AddGroupMemberCommand) -> dict[str, Any]:
        """Add a mcp_server to a group.

        Looks up mcp_server first (outside lock), then acquires lock to
        find the group and call group.add_member().

        Args:
            command: AddGroupMemberCommand with group_id and mcp_server_id.

        Returns:
            Dict with group_id, mcp_server_id, and added flag.

        Raises:
            McpServerNotFoundError: If mcp_server or group does not exist.
        """
        # McpServer lookup outside lock (read-only, thread-safe via repository)
        mcp_server = self._repository.get(command.mcp_server_id)
        if mcp_server is None:
            raise McpServerNotFoundError(command.mcp_server_id)
        if isinstance(getattr(mcp_server, "_peer_identity", None), PeerIdentity):
            # A group holds its member by reference, and the fleet projection
            # replaces a key-pinned server's copy when its key list changes: the
            # group would go on routing to the retired one (ADR-030 §8).
            raise ValidationError(f"mcp_server '{command.mcp_server_id}': a key-pinned server cannot join a group")

        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise McpServerNotFoundError(command.group_id)
            # group.add_member() acquires group's own lock internally
            group.add_member(mcp_server, weight=command.weight, priority=command.priority)

        # Collect GroupMemberAdded event and forward
        publish_group_events(self._event_bus, group)

        logger.info("group_member_added", group_id=command.group_id, mcp_server_id=command.mcp_server_id)
        return {"group_id": command.group_id, "mcp_server_id": command.mcp_server_id, "added": True}


class RemoveGroupMemberHandler(CommandHandler):
    """Handler for RemoveGroupMemberCommand.

    Removes a McpServer from an existing McpServerGroup.
    Raises McpServerNotFoundError if the group does not exist.
    Thread-safe via a per-handler threading.Lock.
    """

    def __init__(self, groups: dict, event_bus: Any) -> None:
        """Initialize the handler.

        Args:
            groups: Shared groups dict mapping group_id to McpServerGroup.
            event_bus: Event bus for publishing domain events.
        """
        self._groups = groups
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def handle(self, command: RemoveGroupMemberCommand) -> dict[str, Any]:
        """Remove a mcp_server from a group.

        Args:
            command: RemoveGroupMemberCommand with group_id and mcp_server_id.

        Returns:
            Dict with group_id, mcp_server_id, and removed flag.

        Raises:
            McpServerNotFoundError: If group does not exist.
        """
        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise McpServerNotFoundError(command.group_id)
            # group.remove_member() acquires group's own lock internally
            group.remove_member(command.mcp_server_id)

        # Collect GroupMemberRemoved event and forward
        publish_group_events(self._event_bus, group)

        logger.info("group_member_removed", group_id=command.group_id, mcp_server_id=command.mcp_server_id)
        return {"group_id": command.group_id, "mcp_server_id": command.mcp_server_id, "removed": True}


# =============================================================================
# Registration
# =============================================================================


def register_crud_handlers(
    command_bus: Any,
    repository: IMcpServerRepository,
    event_bus: Any,
    groups: dict | None = None,
    fleet_writer: IFleetWriter | None = None,
    coordinated: Callable[[], bool] | None = None,
    log_buffers: ILogBuffers | None = None,
    restore_gap: str | None = None,
    declared_in_config: Callable[[str], bool] | None = None,
    peer_identity_authority: IPeerIdentityAuthority | None = None,
    drain: Callable[[str, float], bool] | None = None,
) -> None:
    """Register all mcp_server and group CRUD command handlers with the command bus.

    Args:
        command_bus: Command bus to register handlers on.
        repository: McpServer repository for handler injection.
        event_bus: Event bus for handler injection.
        groups: Groups dict for group handler injection. If None, group handlers
            are not registered.
        fleet_writer: Where fleet changes are recorded so a restart can rebuild
            them. None leaves the fleet in memory only, as before.
        coordinated: Whether this gateway shares its state with peers.
        log_buffers: Where a created server's log buffer is attached (#1747) and
            a deleted server's is released (#1506).
        restore_gap: Why the next start will not read the fleet back, or None
            when it will; reported by the L7 policy push (#1306).
        declared_in_config: Whether the configuration declares a server, which
            the delete handler then refuses (#1744).
        peer_identity_authority: Who may declare or remove a peer identity
            (ADR-030 §9). None refuses every such change.
        drain: Waits for a server's calls in flight before discovery replaces
            it (#2091). None replaces at once.
    """
    # McpServer handlers
    command_bus.register(
        CreateMcpServerCommand,
        CreateMcpServerHandler(
            repository=repository,
            event_bus=event_bus,
            fleet_writer=fleet_writer,
            coordinated=coordinated,
            log_buffers=log_buffers,
            peer_identity_authority=peer_identity_authority,
            drain=drain,
            groups=groups,
        ),
    )
    command_bus.register(
        UpdateMcpServerCommand,
        UpdateMcpServerHandler(
            repository=repository,
            event_bus=event_bus,
            fleet_writer=fleet_writer,
            declared_in_config=declared_in_config,
            peer_identity_authority=peer_identity_authority,
            groups=groups,
        ),
    )
    command_bus.register(
        ForgetPeerKeyNameCommand,
        ForgetPeerKeyNameHandler(
            repository=repository, fleet_writer=fleet_writer, peer_identity_authority=peer_identity_authority
        ),
    )
    command_bus.register(
        SetL7PolicyCommand,
        SetL7PolicyHandler(
            repository=repository, event_bus=event_bus, fleet_writer=fleet_writer, restore_gap=restore_gap
        ),
    )
    command_bus.register(
        DeleteMcpServerCommand,
        DeleteMcpServerHandler(
            repository=repository,
            event_bus=event_bus,
            fleet_writer=fleet_writer,
            log_buffers=log_buffers,
            declared_in_config=declared_in_config,
            peer_identity_authority=peer_identity_authority,
        ),
    )

    # Group handlers (require groups dict)
    if groups is not None:
        command_bus.register(CreateGroupCommand, CreateGroupHandler(groups=groups, event_bus=event_bus))
        command_bus.register(UpdateGroupCommand, UpdateGroupHandler(groups=groups, event_bus=event_bus))
        command_bus.register(DeleteGroupCommand, DeleteGroupHandler(groups=groups, event_bus=event_bus))
        command_bus.register(
            AddGroupMemberCommand,
            AddGroupMemberHandler(repository=repository, groups=groups, event_bus=event_bus),
        )
        command_bus.register(
            RemoveGroupMemberCommand,
            RemoveGroupMemberHandler(groups=groups, event_bus=event_bus),
        )
        logger.info("crud_group_handlers_registered")

    logger.info("crud_mcp_server_handlers_registered")


CreateProviderHandler = CreateMcpServerHandler
UpdateProviderHandler = UpdateMcpServerHandler
DeleteProviderHandler = DeleteMcpServerHandler
