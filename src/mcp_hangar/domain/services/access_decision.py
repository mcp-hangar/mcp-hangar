"""One decision for every surface: may this caller see or call this name on this target? (#1784)

The question used to be assembled from loose strings in ten places across four
modules, so listing and calling could ask different questions and a member
server id could be put in the tenant slot without a type error (#1763).
:func:`decide` is the one entry point. It derives the scopes from a typed
:class:`Target`, takes the tenant from a typed :class:`Caller`, and answers
with a :class:`Decision` in the ADR-029 gate vocabulary. It asks in the order
the executor's gates do -- tool access over every scope, deny wins; then
withdrawal -- and, when both pass, which policy routes the name to a human.
The resolver's merge, cache and invalidation are unchanged: the scope keys are
byte-identical with the executor's.

``decide()`` is pure. Choosing a group's member advances the group's strategy,
so that is done once, before, and arrives as ``Target.member_server``. It reads
through :func:`read_as_one_set`, so one configuration's overlays answer, never
a mix of two (#1431); a surface deciding many names wraps its whole loop too.

Withdrawals are read through :class:`IWithdrawalOverlay`, which
``server/bootstrap`` wires to the tool projection registry. Deciding before it
is wired raises rather than refusing everything: fail-closed and silent is what
hides a wiring bug behind a policy-shaped symptom (#856).
"""

from __future__ import annotations

from collections.abc import Callable

from ..contracts.withdrawals import IWithdrawalOverlay
from ..value_objects.access import GATE_TOOL_ACCESS, GATE_WITHDRAWAL, Caller, Decision, Target
from ..value_objects.tool_access_policy import ToolAccessPolicy
from .governance_overlays import read_as_one_set
from .tool_access_resolver import ToolAccessResolver, get_tool_access_resolver

#: Where the overlay comes from. A getter, not an instance: a test that resets
#: the registry singleton, and the next bootstrap in this process, then read
#: the registry that is current, never the one that was.
_overlay_source: Callable[[], IWithdrawalOverlay] | None = None


def set_withdrawal_overlay_source(source: Callable[[], IWithdrawalOverlay] | None) -> None:
    """Let :func:`decide` read withdrawals from what *source* returns. ``None`` unwires it."""
    global _overlay_source
    _overlay_source = source


def withdrawal_overlay() -> IWithdrawalOverlay:
    """The overlay :func:`decide` reads when none is passed to it; raises when nothing is wired."""
    if _overlay_source is None:
        raise RuntimeError(
            "no withdrawal overlay is wired for the access decision: "
            "server.bootstrap wires the tool projection registry before anything decides (#1784)"
        )
    return _overlay_source()


def decide(
    target: Target,
    name: str,
    caller: Caller,
    *,
    resolver: ToolAccessResolver | None = None,
    withdrawals: IWithdrawalOverlay | None = None,
) -> Decision:
    """May *caller* see and call *name* on *target*?

    *name* is a tool name, a prompt name, or -- for a resource -- the UPSTREAM
    URI, not its ``hangar://`` projection; the same fnmatch patterns match it
    whatever the kind. Only the caller's tenant is consulted. *resolver* and
    *withdrawals* default to the process's.
    """
    policies = resolver if resolver is not None else get_tool_access_resolver()
    overlay = withdrawals if withdrawals is not None else withdrawal_overlay()
    return read_as_one_set(lambda: _decide_now(target, name, caller, policies, overlay))


def _decide_now(
    target: Target, name: str, caller: Caller, resolver: ToolAccessResolver, overlay: IWithdrawalOverlay
) -> Decision:
    tenant_id = caller.tenant_id
    allowed = all(
        resolver.is_allowed(
            server_id, name, kind=target.kind, group_id=group_id, member_id=tenant_id, member_server_id=member_server
        )
        for server_id, group_id, member_server in target.policy_scopes()
    )
    if not allowed:
        return Decision.refused_by(GATE_TOOL_ACCESS)
    if _withdrawn(overlay, target, name, tenant_id):
        return Decision.refused_by(GATE_WITHDRAWAL)
    return Decision.allow(approval_policy=_approval_policy(resolver, target, name, tenant_id))


def _withdrawn(overlay: IWithdrawalOverlay, target: Target, name: str, tenant_id: str | None) -> bool:
    """Whether *name* is withdrawn for *tenant_id* on this target (#231).

    Asked under the id the caller named, under the member the group selected
    (a tool's projection lives under the member, so a withdrawal declared there
    is read there, #1040), and under every group that owns a server named
    directly. ``None`` -- a tool the catalogue does not know -- blocks nothing.
    """
    declared_under = (target.addressed, *(() if target.member_server is None else (target.member_server,)))
    return any(
        overlay.withdrawal_status(scope, name, tenant_id, kind=target.kind)
        for scope in (*declared_under, *target.owning_groups)
    )


def _approval_policy(
    resolver: ToolAccessResolver, target: Target, name: str, tenant_id: str | None
) -> ToolAccessPolicy | None:
    """The effective policy of the first scope whose approval list holds *name*, or None.

    The first scope that asks supplies the timeout and channel. No `_global`
    second lookup: `_compute_effective_policy` merges `_global` into every scope.
    """
    for server_id, group_id, member_server in target.policy_scopes():
        scoped = resolver.resolve_effective_policy(
            server_id, group_id, tenant_id, member_server_id=member_server, kind=target.kind
        )
        if not scoped.is_unrestricted() and scoped.requires_approval(name):
            return scoped
    return None
