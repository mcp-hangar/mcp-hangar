"""Port for the REST endpoint's stored tool-access policies of a removed server (#2298).

A command handler may not reach the policy store or the lease keeper, both
above `application`. `hangar_unload` and the REST delete say *when* a server's
stored rows go; the adapter in `server/config.py` decides *whether*: only where
no peer reads the store, as a reload does since #2224.
"""

from __future__ import annotations

from typing import Protocol


class IStoredToolAccessPolicies(Protocol):
    """Forgets the stored tool-access policies of a server that was removed."""

    def forget(self, mcp_server_id: str) -> None:
        """Delete the server's own stored rows, unless a peer may read them.

        The id is free for a later server, which must not inherit them at the
        next restart (#1028). Does not fail: a row left behind only narrows.
        """
