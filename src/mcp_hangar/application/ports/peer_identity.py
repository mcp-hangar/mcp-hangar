"""Who may declare or change an upstream's peer identity (ADR-030 §9).

A key list decides which upstream a call may reach, so declaring one is a
narrower right than registering a server. The command handlers ask this port;
the delivery layer answers from the same authorization components the REST
routes use, so the application layer never reaches into the server package.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ...domain.value_objects.security import Principal


class IPeerIdentityAuthority(Protocol):
    """Whether *principal* holds `mcp_servers:peer_identity`."""

    def may_declare(self, principal: "Principal | None") -> bool:
        """True when *principal* may set, change or remove a peer identity.

        With authentication disabled every caller may, exactly as every REST
        route allows every caller then. With it enabled, only an authenticated
        principal holding the permission may.
        """
        ...
