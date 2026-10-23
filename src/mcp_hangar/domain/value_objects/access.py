"""Who asks, what they ask about, and what was decided (#1784).

Every surface that shows or serves a name asks one question: may this caller
see or call this name of this kind on this target? Until #1784 each surface
assembled it from loose strings, ``(server id, group id, member server id)``
plus the tenant as a fourth, and nothing stopped a member server id from
landing in the tenant slot -- how every scope-key defect so far happened
(#1763). :class:`Target`, :class:`Caller` and :class:`Decision` make the
question and its answer typed; ``decide()`` in
``domain/services/access_decision.py`` is the one function between them.

The gate vocabulary below is spelled here and in
``observability/conventions.py`` (``Gate.TOOL_ACCESS``, ``Gate.WITHDRAWAL``,
``Gate.ALLOW``, ``Gate.DENY``, ``GATE_REASONS``), because ADR-027 keeps the
domain from importing that module. A test keeps the two spellings equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .tool_access_policy import ToolAccessPolicy

# What a policy governs. A policy is keyed `(scope target, kind)`; the default
# everywhere is "tool", which is what every pre-#1028 registration meant.
PolicyKind = Literal["tool", "prompt", "resource"]
POLICY_KINDS: tuple[PolicyKind, ...] = ("tool", "prompt", "resource")
DEFAULT_KIND: PolicyKind = "tool"

TargetKind = Literal["server", "group"]

#: ``(server id, group id, member server id)``: one question to the resolver,
#: asked with the caller's tenant. The shape `resolve_effective_policy` takes.
PolicyScope = tuple[str, str | None, str | None]

#: The gates a Decision can name: stages of the executor's `_GATES` without
#: their `_gate_` prefix (ADR-029 s5), each with the one bounded
#: `hangar.gate.reason` code it refuses with.
GATE_TOOL_ACCESS = "tool_access"
GATE_WITHDRAWAL = "withdrawal"
REASON_TOOL_ACCESS = "tool_not_in_access_policy"
REASON_WITHDRAWN = "tool_withdrawn"
DECISION_REASONS: dict[str, str] = {GATE_TOOL_ACCESS: REASON_TOOL_ACCESS, GATE_WITHDRAWAL: REASON_WITHDRAWN}
#: `hangar.gate.outcome`, as the gate event spells it.
OUTCOME_ALLOW = "allow"
OUTCOME_DENY = "deny"


def _identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class Target:
    """What a caller asked about, resolved as far as a call resolves it.

    ``addressed`` is the id the caller named, a server or a group. For a group,
    ``member_server`` is the member the call is routed to once selected, or
    ``None`` while none is chosen; a server named directly never has one. ``owning_groups`` are the groups that own a
    server named directly, in configuration order, read together with the
    overlays (#1488); a group has none. ``kind`` is what the name is.
    """

    addressed: str
    addressed_kind: TargetKind
    member_server: str | None = None
    owning_groups: tuple[str, ...] = ()
    kind: PolicyKind = DEFAULT_KIND

    def __post_init__(self) -> None:
        _identifier(self.addressed, "Target.addressed")
        if self.addressed_kind not in ("server", "group"):
            raise ValueError(f"Target.addressed_kind must be 'server' or 'group', got {self.addressed_kind!r}")
        if self.kind not in POLICY_KINDS:
            raise ValueError(f"Target.kind must be one of {POLICY_KINDS}, got {self.kind!r}")
        if not isinstance(self.owning_groups, tuple):
            raise ValueError(f"Target.owning_groups must be a tuple, got {type(self.owning_groups).__name__}")
        for group_id in self.owning_groups:
            _identifier(group_id, "Target.owning_groups entry")
        if len(set(self.owning_groups)) != len(self.owning_groups):
            raise ValueError(f"Target.owning_groups names a group twice: {self.owning_groups!r}")
        if self.addressed in self.owning_groups:
            raise ValueError(f"Target {self.addressed!r} cannot own itself")
        if self.member_server is not None:
            _identifier(self.member_server, "Target.member_server")
            if self.member_server == self.addressed:
                raise ValueError(f"Target.member_server {self.member_server!r} is the group it was selected from")
        if self.addressed_kind == "group" and self.owning_groups:
            raise ValueError(f"a group is owned by no group; Target.owning_groups is {self.owning_groups!r}")
        if self.addressed_kind == "server" and self.member_server is not None:
            raise ValueError("a server named directly is dispatched to as itself; it selects no member")

    @classmethod
    def server(cls, server_id: str, *, owning_groups: tuple[str, ...] = (), kind: PolicyKind = DEFAULT_KIND) -> Target:
        """A server named directly, with the groups that own it."""
        return cls(server_id, "server", owning_groups=owning_groups, kind=kind)

    @classmethod
    def group(cls, group_id: str, *, member_server: str | None = None, kind: PolicyKind = DEFAULT_KIND) -> Target:
        """A group, with the member selected for the call, when one is."""
        return cls(group_id, "group", member_server=member_server, kind=kind)

    @property
    def is_group(self) -> bool:
        return self.addressed_kind == "group"

    def policy_scopes(self) -> tuple[PolicyScope, ...]:
        """Every scope the access policy is resolved under, each asked with the caller's tenant.

        A group has one scope: the group and the member selected for the call,
        whose policy is keyed by its SERVER id (#1164). A server has its own,
        plus one per owning group, asked as a call naming that group and routed
        to this member is asked. A name is allowed only when every scope allows
        it: deny wins.
        """
        if self.is_group:
            return ((self.addressed, self.addressed, self.member_server),)
        return (
            (self.addressed, None, None),
            *((group_id, group_id, self.addressed) for group_id in self.owning_groups),
        )


@dataclass(frozen=True)
class Caller:
    """Who asks.

    ``tenant_id`` is the ONLY place a tenant goes; in ``front_door`` topology
    its absence is the resolver's fail-closed missing-identity branch.
    ``roles`` are carried for the record and not consulted by the access
    decision: role-based authorization stays in ``tool:invoke`` (maintainer
    decision on #1763, 2026-10-23).
    """

    principal_id: str | None = None
    tenant_id: str | None = None
    roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.principal_id is not None:
            _identifier(self.principal_id, "Caller.principal_id")
        if self.tenant_id is not None:
            _identifier(self.tenant_id, "Caller.tenant_id")
        if not isinstance(self.roles, frozenset):
            raise ValueError(f"Caller.roles must be a frozenset, got {type(self.roles).__name__}")
        for role in self.roles:
            _identifier(role, "Caller.roles entry")


@dataclass(frozen=True)
class Decision:
    """What was decided about one name on one target for one caller.

    When refused, ``gate`` is the stage that refused, ``GATE_TOOL_ACCESS`` or
    ``GATE_WITHDRAWAL`` (ADR-029 s5), and ``reason`` is that gate's bounded
    ``hangar.gate.reason`` code, never free text. When
    allowed, ``approval_policy`` is the effective policy that routes the name
    to a human before it runs, or ``None``; that is observational, not a
    refusal: an approval-required name is allowed, and listed, as it always was.
    """

    allowed: bool
    gate: str | None = None
    reason: str | None = None
    approval_policy: ToolAccessPolicy | None = None

    def __post_init__(self) -> None:
        if self.allowed:
            if self.gate is not None or self.reason is not None:
                raise ValueError(f"an allowed Decision names no gate, got gate={self.gate!r} reason={self.reason!r}")
            return
        if self.gate not in DECISION_REASONS:
            raise ValueError(f"Decision.gate must be one of {tuple(DECISION_REASONS)}, got {self.gate!r}")
        if self.reason != DECISION_REASONS[self.gate]:
            raise ValueError(f"gate {self.gate!r} refuses with {DECISION_REASONS[self.gate]!r}, got {self.reason!r}")
        if self.approval_policy is not None:
            raise ValueError("a refused Decision carries no approval policy")

    @classmethod
    def allow(cls, *, approval_policy: ToolAccessPolicy | None = None) -> Decision:
        return cls(True, approval_policy=approval_policy)

    @classmethod
    def refused_by(cls, gate: str) -> Decision:
        """The refusal of *gate*, with the one reason that gate refuses with."""
        if gate not in DECISION_REASONS:
            raise ValueError(f"Decision.gate must be one of {tuple(DECISION_REASONS)}, got {gate!r}")
        return cls(False, gate=gate, reason=DECISION_REASONS[gate])

    @property
    def outcome(self) -> str:
        """`hangar.gate.outcome`: ``allow`` or ``deny``."""
        return OUTCOME_ALLOW if self.allowed else OUTCOME_DENY

    @property
    def withdrawn(self) -> bool:
        return self.gate == GATE_WITHDRAWAL
