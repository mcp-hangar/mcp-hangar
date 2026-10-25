"""The withdrawal overlay, as the access decision reads it (#1784).

Withdrawals live in the tool projection registry, an application read model.
``decide()`` is a domain service and may not import it: the one such edge is
in the ``.importlinter`` debt ledger, and the ledger is capped. So the decision
reads withdrawals through this port, which the registry implements, and
``server/bootstrap`` wires the two together.
"""

from abc import ABC, abstractmethod


class IWithdrawalOverlay(ABC):
    """What the overlays say about one name, for one tenant."""

    @abstractmethod
    def withdrawal_status(
        self, mcp_server: str, name: str, tenant_id: str | None, *, kind: str = "tool"
    ) -> bool | None:
        """Whether *name* on *mcp_server* is withdrawn for *tenant_id*, or ``None`` when the catalogue does not know it.

        For a tool this is the projection's status as a call resolves it: the
        base status set at discovery, its per-tenant overrides, the config and
        runtime overlays, and the pins that withhold a tool new since they were
        taken (#1801). ``None`` means the tool is unknown -- not discovered and
        not withdrawn -- which blocks nothing, so a registry that was never
        populated is the safe default it always was. A tool the pins do not
        admit is withdrawn (``True``) even before the catalogue knows it.

        For a prompt or a resource, which are relayed live and have no
        projection, it is the overlays alone, and never ``None``.

        Args:
            mcp_server: The id a withdrawal can be declared under: a server, or a group.
            name: A tool name, a prompt name, or an upstream resource URI.
            tenant_id: The caller's tenant, or ``None`` with no identity.
            kind: What *name* is: ``tool``, ``prompt`` or ``resource``.
        """
