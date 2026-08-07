"""CQRS command handlers for McpServer and Group CRUD operations.

All handlers:
- Receive dependencies via constructor (Dependency Inversion Principle)
- Emit domain events via the event bus after successful mutations
- Raise domain exceptions on invalid operations (not found, duplicate, etc.)
- Are thread-safe (mutations guarded via repository or injected lock)
"""

import threading
from collections.abc import Callable
from typing import Any, cast

from ...domain.events import (
    EgressPolicyCleared,
    EgressPolicySet,
    McpServerDeregistered,
    McpServerRegistered,
)
from ...domain.exceptions import McpServerNotFoundError, ValidationError
from ...domain.model.mcp_server import McpServer
from ...domain.model.mcp_server_group import GroupDeleted, McpServerGroup
from ...domain.repository import IMcpServerRepository
from ...domain.security.ssrf import validate_no_ssrf
from ...domain.value_objects.provenance import Provenance
from ...domain.value_objects import LoadBalancerStrategy, McpServerMode, McpServerState
from ...domain.contracts.command import CommandHandler
from ...domain.contracts.fleet import IFleetWriter
from ...domain.services.fleet_snapshot import snapshot_of
from ...logging_config import get_logger
from ...stream_ids import MCP_SERVER, MCP_SERVER_GROUP
from .crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    CreateMcpServerCommand,
    DeleteGroupCommand,
    DeleteMcpServerCommand,
    RemoveGroupMemberCommand,
    SetL7PolicyCommand,
    UpdateGroupCommand,
    UpdateMcpServerCommand,
)

logger = get_logger(__name__)


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
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer
        self._coordinated = coordinated or (lambda: False)

    def handle(self, command: CreateMcpServerCommand) -> dict[str, Any]:
        """Create a new mcp_server.

        Args:
            command: CreateMcpServerCommand with mcp_server configuration.

        Returns:
            Dict with mcp_server_id and created flag.

        Raises:
            ValidationError: If a mcp_server with the same ID already exists.
        """
        if self._repository.exists(command.mcp_server_id):
            raise ValidationError(f"McpServer already exists: {command.mcp_server_id}")

        self._refuse_local_mode_when_coordinating(command.mode)

        if McpServerMode.normalize(command.mode) == McpServerMode.REMOTE and command.endpoint is not None:
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
        )

        # Recorded before it joins the fleet, and before the event. The order is
        # the point: a write that fails leaves nothing behind and the caller is
        # told the registration did not happen. The other order gives a running
        # server, a published event, and no record -- which is what a restart
        # then quietly resolves by forgetting the server.
        if self._fleet_writer is not None:
            self._fleet_writer.save(snapshot_of(mcp_server))

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
    ) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository for persistence.
            event_bus: Event bus for publishing domain events.
            fleet_writer: Where the changed configuration is recorded. Without
                it an update survives only until the process ends, and a restart
                silently reverts it.
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer

    def _get_mcp_server_or_raise(self, mcp_server_id: str) -> McpServer:
        """Retrieve mcp_server or raise McpServerNotFoundError.

        Args:
            mcp_server_id: ID of the mcp_server to retrieve.

        Returns:
            McpServer instance.

        Raises:
            McpServerNotFoundError: If no mcp_server with given ID exists.
        """
        mcp_server = self._repository.get(mcp_server_id)
        if mcp_server is None:
            raise McpServerNotFoundError(mcp_server_id)
        return cast(McpServer, mcp_server)

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
        mcp_server = self._get_mcp_server_or_raise(command.mcp_server_id)

        mcp_server.update_config(
            description=command.description,
            env=command.env,
            idle_ttl_s=command.idle_ttl_s,
            health_check_interval_s=command.health_check_interval_s,
        )

        # After the aggregate has changed, not before: the record is of what the
        # server now is. An update that is not recorded is reverted by the next
        # restart, which reads a snapshot describing the server as it used to be.
        if self._fleet_writer is not None:
            self._fleet_writer.save(snapshot_of(mcp_server))

        # Collect the McpServerUpdated event recorded by update_config() and
        # append it to the aggregate's stream, which also delivers it.
        self._event_bus.publish_aggregate_events(MCP_SERVER, mcp_server.mcp_server_id, mcp_server.collect_events())

        logger.info(
            "mcp_server_updated",
            mcp_server_id=command.mcp_server_id,
            source=command.source,
        )
        return {"mcp_server_id": command.mcp_server_id, "updated": True}


class SetL7PolicyHandler(CommandHandler):
    """Handler for SetL7PolicyCommand.

    Attaches, replaces, or clears the L7 egress policy on an existing McpServer
    via McpServer.set_l7_policy(). Raises McpServerNotFoundError if not found.
    """

    def __init__(self, repository: IMcpServerRepository, event_bus: Any) -> None:
        self._repository = repository
        self._event_bus = event_bus

    def handle(self, command: SetL7PolicyCommand) -> dict[str, Any]:
        """Set (or clear) the mcp_server's L7 policy.

        Raises:
            McpServerNotFoundError: If mcp_server does not exist.
        """
        mcp_server = self._repository.get(command.mcp_server_id)
        if mcp_server is None:
            raise McpServerNotFoundError(command.mcp_server_id)

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
                )
            )

        logger.info(
            "mcp_server_l7_policy_set",
            mcp_server_id=command.mcp_server_id,
            cleared=command.policy is None,
            source=command.source,
        )
        return {"mcp_server_id": command.mcp_server_id, "l7_policy_set": command.policy is not None}


class DeleteMcpServerHandler(CommandHandler):
    """Handler for DeleteMcpServerCommand.

    Stops a running mcp_server (if not COLD or DEAD), then removes it from the
    repository and emits McpServerDeregistered.
    Raises McpServerNotFoundError if the mcp_server does not exist.
    """

    def __init__(
        self,
        repository: IMcpServerRepository,
        event_bus: Any,
        fleet_writer: IFleetWriter | None = None,
    ) -> None:
        """Initialize the handler.

        Args:
            repository: McpServer repository for persistence.
            event_bus: Event bus for publishing domain events.
            fleet_writer: Where the removal is recorded. A row left behind
                resurrects the server on the next restart.
        """
        self._repository = repository
        self._event_bus = event_bus
        self._fleet_writer = fleet_writer

    def _get_mcp_server_or_raise(self, mcp_server_id: str) -> McpServer:
        """Retrieve mcp_server or raise McpServerNotFoundError.

        Args:
            mcp_server_id: ID of the mcp_server to retrieve.

        Returns:
            McpServer instance.

        Raises:
            McpServerNotFoundError: If no mcp_server with given ID exists.
        """
        mcp_server = self._repository.get(mcp_server_id)
        if mcp_server is None:
            raise McpServerNotFoundError(mcp_server_id)
        return cast(McpServer, mcp_server)

    def handle(self, command: DeleteMcpServerCommand) -> dict[str, Any]:
        """Delete a mcp_server, stopping it first if running.

        Args:
            command: DeleteMcpServerCommand with mcp_server_id to delete.

        Returns:
            Dict with mcp_server_id and deleted flag.

        Raises:
            McpServerNotFoundError: If mcp_server does not exist.
        """
        mcp_server = self._get_mcp_server_or_raise(command.mcp_server_id)

        # Stop mcp_server if it is in a running state (not COLD or DEAD).
        # I/O (shutdown) is done outside the repository lock to respect
        # the no-I/O-under-lock rule.
        if mcp_server.state not in (McpServerState.COLD, McpServerState.DEAD):
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
        self._event_bus.publish_aggregate_events(MCP_SERVER_GROUP, group.id, group.collect_events())

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
        self._event_bus.publish_aggregate_events(MCP_SERVER_GROUP, group.id, group.collect_events())

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
        self._event_bus.publish_aggregate_events(MCP_SERVER_GROUP, group.id, group.collect_events())
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

        with self._lock:
            group = self._groups.get(command.group_id)
            if group is None:
                raise McpServerNotFoundError(command.group_id)
            # group.add_member() acquires group's own lock internally
            group.add_member(mcp_server, weight=command.weight, priority=command.priority)

        # Collect GroupMemberAdded event and forward
        self._event_bus.publish_aggregate_events(MCP_SERVER_GROUP, group.id, group.collect_events())

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
        self._event_bus.publish_aggregate_events(MCP_SERVER_GROUP, group.id, group.collect_events())

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
    """
    # McpServer handlers
    command_bus.register(
        CreateMcpServerCommand,
        CreateMcpServerHandler(
            repository=repository, event_bus=event_bus, fleet_writer=fleet_writer, coordinated=coordinated
        ),
    )
    command_bus.register(
        UpdateMcpServerCommand,
        UpdateMcpServerHandler(repository=repository, event_bus=event_bus, fleet_writer=fleet_writer),
    )
    command_bus.register(SetL7PolicyCommand, SetL7PolicyHandler(repository=repository, event_bus=event_bus))
    command_bus.register(
        DeleteMcpServerCommand,
        DeleteMcpServerHandler(repository=repository, event_bus=event_bus, fleet_writer=fleet_writer),
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
