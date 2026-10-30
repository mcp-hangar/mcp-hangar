"""Writing down which servers the gateway is supposed to have.

`RecoveryService.recover_mcp_servers` reads a table of configurations on every
start and registers what it finds. Nothing filled that table:
`save_mcp_server_config` has no caller outside a unit test, so a server
registered through the API or by discovery lived only in memory and was gone
after a restart. The event log recorded that it had been registered, which is
why the trail looked complete while the fleet was not.

This is the write side, and it is **synchronous on purpose**. The command bus is
sync end to end -- the API dispatches into a threadpool and discovery calls
`send` directly -- so a port that could only be awaited would either have to be
faked at the call site or fired and forgotten. Fired and forgotten is the shape
that produced this defect in the first place: a registration that reports
success while its durable half fails silently. The adapter bridges to whatever
the storage backend's repository actually is.
"""

from abc import ABC, abstractmethod

from .persistence import GroupMemberRecord, GroupRecord, McpServerConfigSnapshot, PeerKeyClaim


class NotTheManagerError(RuntimeError):
    """A convergence loop tried to write while this instance was not managing.

    Raised rather than returning False: a caller that treats "the deletion did
    not happen" as ordinary would carry on as though it had, and the whole point
    of the refusal is that this instance's view is out of date.
    """

    def __init__(self, mcp_server_id: str) -> None:
        super().__init__(
            f"refusing to deregister {mcp_server_id}: this instance does not hold the management lease. "
            "It was decided under a tenure that has since ended, so the fleet it describes is not the current one."
        )


class IGroupWriter(ABC):
    """Records what the group API does, for a restart (#2172): sync and raising, as `IFleetWriter`.

    An operator's requests, so not fenced: see `IFleetWriter.delete`.
    """

    @abstractmethod
    def create_group(self, record: GroupRecord) -> bool:
        """Store a new group. False when a row of that id exists."""

    @abstractmethod
    def update_group(
        self, group_id: str, strategy: str | None, min_healthy: int | None, description: str | None
    ) -> None:
        """Change a stored group's given fields."""

    @abstractmethod
    def save_group_member(self, record: GroupMemberRecord) -> None:
        """Store a member."""

    @abstractmethod
    def delete_group(self, group_id: str) -> None:
        """Remove a group's row and its member rows."""

    @abstractmethod
    def delete_group_member(self, group_id: str, member_id: str) -> None:
        """Remove a member's row."""


class IFleetWriter(ABC):
    """Records the fleet as it changes, so a restart can rebuild it."""

    @abstractmethod
    def save(self, snapshot: McpServerConfigSnapshot) -> None:
        """Write a server's configuration, replacing any earlier one.

        Raises rather than reporting failure through a log line: a registration
        whose durable half did not happen must not answer "created".

        Args:
            snapshot: The configuration as it should be restored.
        """

    @abstractmethod
    def read(self, mcp_server_id: str) -> tuple[McpServerConfigSnapshot, int] | None:
        """Read a live server's recorded configuration and its version.

        A change to one field is built from this, not from the replica's own
        copy, which may be behind a peer's write (#1954).

        Args:
            mcp_server_id: The server to read.

        Returns:
            The recorded configuration and its version, or None when there is
            no live row.
        """

    @abstractmethod
    def save_versioned(
        self,
        snapshot: McpServerConfigSnapshot,
        expected_version: int | None,
        peer_key: PeerKeyClaim = PeerKeyClaim.CLAIM,
    ) -> None:
        """Write a configuration only if the row is still the one that was read.

        Args:
            snapshot: The configuration to record.
            expected_version: The version `read` returned, 0 when it returned
                nothing, or None for a create (see `IMcpServerConfigRepository`).

        Raises:
            ConcurrentModificationError: The row changed since it was read.
            PeerKeyNameRefused: The peer-key name record contradicts the write.
        """

    @abstractmethod
    def forget_peer_key_name(self, mcp_server_id: str) -> bool:
        """Remove a name's entry from the peer-key name record (see `IMcpServerConfigRepository`)."""

    @abstractmethod
    def delete(self, mcp_server_id: str, *, fenced: bool = False) -> None:
        """Remove a server's configuration.

        A deregistration that leaves the row behind resurrects the server on the
        next restart, which is worse than never having persisted it.

        `fenced` is for deletions a *convergence loop* decided on, as opposed to
        ones an operator asked for. The distinction matters because of one
        sequence:

        1. A holds the management lease and decides server X has expired.
        2. A stalls -- a stop-the-world pause, a wedged disk.
        3. The lease expires. B acquires it and re-registers X, which is alive.
        4. A resumes and issues its delete.

        A's own lease keeper cannot save it here: it was frozen too, and the
        delete goes out before its next tick. The check has to be *inside* the
        write, which is what fencing means -- the deletion carries the tenure it
        was decided under, and lands only if that tenure is still current.

        An operator's deletion is not fenced: they are not a stale loop
        finishing, and refusing their request on two pods out of three would
        make the API answer differently depending on which one they reached.

        Args:
            mcp_server_id: The server being removed.
            fenced: Whether this deletion must prove the caller still holds the
                management lease.

        Raises:
            NotTheManagerError: When `fenced` and this instance is coordinating
                but does not currently hold the lease.
        """
