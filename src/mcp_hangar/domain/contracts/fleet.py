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

from .persistence import McpServerConfigSnapshot


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
