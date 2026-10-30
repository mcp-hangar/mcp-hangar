"""Asking `decide()` about a resolver's policies alone (#1784).

A test about access policy has no projection registry to hand, and `decide()`
asks for withdrawals as well. Here nothing is withdrawn, so the answer is the
policy's and nothing else's -- the question these tests asked of the resolver
before its loose-string signatures went.
"""

from __future__ import annotations

from mcp_hangar.domain.contracts.withdrawals import IWithdrawalOverlay
from mcp_hangar.domain.services.access_decision import decide
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects.access import Caller, PolicyKind, Target


class NothingWithdrawn(IWithdrawalOverlay):
    def withdrawal_status(
        self, mcp_server_id: str, name: str, tenant_id: str | None, *, kind: PolicyKind = "tool"
    ) -> bool | None:
        return None


def may(resolver: ToolAccessResolver, target: Target, name: str, caller: Caller | None = None) -> bool:
    """Whether *caller*, nobody by default, is allowed *name* on *target* by policy."""
    return decide(target, name, caller or Caller(), resolver=resolver, withdrawals=NothingWithdrawn()).allowed
