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
    def delete(self, mcp_server_id: str) -> None:
        """Remove a server's configuration.

        A deregistration that leaves the row behind resurrects the server on the
        next restart, which is worse than never having persisted it.

        Args:
            mcp_server_id: The server being removed.
        """
