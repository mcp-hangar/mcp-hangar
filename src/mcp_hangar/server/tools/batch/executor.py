"""Batch execution engine.

Provides parallel execution of batch invocations with:
- ThreadPoolExecutor for concurrent execution
- Two-level semaphore concurrency control (global + per-mcp_server)
- Single-flight pattern for cold starts
- Cooperative cancellation
- Circuit breaker integration
- Response truncation
"""

import asyncio
import contextvars
import json
import threading
import time
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, TypeVar, cast

from ....application.commands import InvokeToolCommand, StartMcpServerCommand
from ....application.group_events import publish_group_events
from ....application.read_models.tool_projection import get_tool_projection_registry
from ....application.services.mutator_pipeline import MutatorPipeline
from ....application.services.validator_pipeline import ValidatorPipeline
from ....application.tasks.tool_pin_context import CurrentToolPin, get_current_tool_pin, set_current_tool_pin
from ....context import bind_routing_headers, get_identity_context, release_routing_headers
from ....domain.contracts.mutator import MutationContext
from ....domain.contracts.validator import ValidationContext
from ....domain.events import (
    BatchCallCompleted,
    BatchInvocationCompleted,
    BatchInvocationRequested,
    ToolWithdrawnRejected,
)
from ....domain.exceptions import CannotStartMcpServerError
from ....domain.model.mcp_server import (
    DEAD_NOT_REVIVED_BY_CALLS,
    START_REFUSED_IN_BACKOFF,
    START_REFUSED_NOT_REVIVED_BY_CALLS,
)
from ....domain.services import get_tool_access_resolver
from ....domain.services.digest_validator import DigestValidator
from ....domain.services.governance_overlays import read_as_one_set
from ....domain.value_objects import DigestEnforcement, DigestPolicy, DigestUnknownPolicy
from ....domain.value_objects.truncation import ContinuationOwner
from ....infrastructure.single_flight import SingleFlight
from ....logging_config import get_logger
from ....metrics import (
    BATCH_CALLS_TOTAL,
    BATCH_CANCELLATIONS_TOTAL,
    BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL,
    BATCH_CONCURRENCY_GAUGE,
    BATCH_DURATION_SECONDS,
    BATCH_SIZE_HISTOGRAM,
    BATCH_TRUNCATIONS_TOTAL,
    TENANT_QUOTA_REFUSALS_TOTAL,
    TOOL_ACCESS_DENIED_TOTAL,
)
from ....negotiation import (
    read_protocol_negotiation,
    reset_current_protocol_negotiation,
    set_current_protocol_negotiation,
)
from ....observability.tracing import extract_trace_context, get_tracer, mark_span_error, record_handled_failure
from ....retry import RetryPolicy, RetryResult, configured_retry_policy, retry_sync
from ...context import get_context
from ...state import GROUPS
from .concurrency import ConcurrencyManager, get_concurrency_manager
from .member_health import MemberOutcome, member_outcome
from .models import MAX_RESPONSE_SIZE_BYTES, BatchResult, CallResult, CallSpec, RelayCapture, RetryMetadata
from .relay_seam import upstream_task
from .tenant_admission import CONCURRENCY, NO_BUDGET, RATE, Grant, Refusal, Reservation, get_tenant_admission

logger = get_logger(__name__)

#: What a caller refused by its tenant's execution budget is told, by reason (#1445).
_TENANT_QUOTA_MESSAGES = {
    NO_BUDGET: "No execution budget is configured for this tenant",
    CONCURRENCY: "This tenant's execution budget is exhausted: too many calls in flight",
    RATE: "This tenant's execution budget is exhausted: calls started too fast",
}


def _inbound_trace_meta(ctx: Any) -> dict[str, str]:
    """Read SEP-414 trace keys from the inbound request's ``params._meta``.

    Returns only ``traceparent``/``tracestate`` (``baggage`` is deliberately
    excluded pending cross-tenant scrubbing). Best-effort fault barrier: trace
    context is a convention (SEP-414 MAY), so any failure to read it returns
    ``{}`` and never breaks the call.
    """
    try:
        req_meta = ctx.request_context.meta
        if req_meta is None:
            return {}
        dumped = req_meta.model_dump(exclude_none=True) if hasattr(req_meta, "model_dump") else dict(req_meta)
        return {k: str(v) for k, v in dumped.items() if k in ("traceparent", "tracestate") and isinstance(v, str)}
    except Exception:  # noqa: BLE001 -- fault barrier: trace reading must not break invocation
        return {}


def _call_span_parent(carrier_context: Any) -> dict[str, Any]:
    """Keyword arguments placing a ``batch.call`` span in its trace (#1270).

    A valid ambient span is the parent: ``batch.execute`` in the worker, whose
    context the worker inherited, under the SDK's SERVER span that already
    parented the request on the caller's ``_meta``. The extracted carrier parents
    the span only when there is no valid ambient span, as for a direct executor
    caller. A carrier naming another trace than the ambient span is kept as a
    link rather than dropped. ``None`` means tracing is off: no OTel state is read.
    """
    if carrier_context is None:
        return {}
    from opentelemetry import trace

    ambient = trace.get_current_span().get_span_context()
    if not ambient.is_valid:
        return {"context": carrier_context}
    carried = trace.get_current_span(carrier_context).get_span_context()
    if carried.is_valid and carried.trace_id != ambient.trace_id:
        return {"links": [trace.Link(carried)]}
    return {}


def _inbound_meta_dict(ctx: Any) -> dict[str, Any] | None:
    """Return the inbound request's ``params._meta`` as a plain dict, or ``None``.

    Best-effort fault barrier mirroring ``_inbound_trace_meta``: pydantic ``Meta``
    models are dumped, plain mappings are copied, and any failure yields ``None``
    so a missing/malformed ``_meta`` never breaks the call.
    """
    try:
        req_meta = ctx.request_context.meta
        if req_meta is None:
            return None
        if hasattr(req_meta, "model_dump"):
            return dict(req_meta.model_dump(exclude_none=True))
        return dict(req_meta)
    except Exception:  # noqa: BLE001 -- fault barrier: meta reading must not break invocation
        return None


def _is_task_result(result: dict[str, Any]) -> bool:
    """Return True if an upstream ``tools/call`` result is an MCP task handle.

    In either shape an upstream sends one: the current flat ``resultType: "task"``
    result, or the older nested ``{"task": {...}}``. The relay seam's
    :func:`~.relay_seam.upstream_task` decides, so what is captured here is what
    the seam registers (#1405).
    """
    return upstream_task(result) is not None


#: Per worker thread: the approval id the gate granted for the call this thread
#: is running, read at dispatch. Holds no event loop -- see
#: `_run_approval_coroutine`.
_approval_loop_local = threading.local()

_T = TypeVar("_T")


def _run_approval_coroutine(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run one approval-gate coroutine to completion on an event loop of its own.

    The loop is torn down before this returns, however the coroutine ends:
    pending tasks cancelled, async generators closed, the default executor
    shut down and its thread joined, and the loop closed with its selector.
    The default executor is where `ApprovalHoldRegistry.wait_slice` waits,
    through `asyncio.to_thread`.

    Before #1452 the loop was kept per worker thread and closed only at
    interpreter exit. `execute()` builds a new ThreadPoolExecutor per batch,
    so nearly every held call ran on a fresh thread and left its loop behind,
    with the loop's file descriptors and an idle `asyncio_0` thread, for the
    life of the process.

    A loop per run rather than one shared, long-lived loop: the gate's
    coroutines read storage and send deliveries that may block, and every
    hold waits in the loop's default executor. On one shared loop a slow call
    would stall every other held call, and the default executor's worker cap
    would queue their waits past their slices. A loop of its own keeps each
    hold as isolated as it was. The cost is one new selector per run, paid
    only by a call that is actually held for a human, or revalidated after one.

    Cross-loop signalling is unchanged: the hold registry uses
    `threading.Event`, not `asyncio.Event`, because `resolve()` runs on
    FastMCP's main loop while `check()` waits here on a different loop.

    `loop_factory` leaves the thread's current-loop slot alone, as the
    `asyncio.new_event_loop()` this replaces did. `asyncio.run()` would set it
    and then clear it, which a caller on the main thread would notice.
    """
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
        return runner.run(coro)


#: Sentinel: "not looked up yet", distinct from a genuine "no such projection".
_UNRESOLVED = object()


def owners_by_member() -> dict[str, tuple[str, ...]]:
    """Each group member's server id, mapped to the ids of every group that owns it.

    The groups are in config order. Read from the ``GROUPS`` that
    ``_gate_resolve_target`` resolves a group id against. ``hangar_call`` and
    the front door both read group ownership from here, so the two cannot
    disagree about which groups govern a member.
    """
    owners: dict[str, tuple[str, ...]] = {}
    for group_id, group in list(GROUPS.items()):
        for member in group.members:
            held = owners.get(member.id, ())
            if group_id not in held:
                owners[member.id] = (*held, group_id)
    return owners


def _groups_owning(server_id: str) -> tuple[str, ...]:
    """The ids of every group *server_id* is a member of.

    A group member is in the server repository like any other server, so a
    caller can name it directly instead of naming its group. The call still
    goes to that member, but the member is governed by each group that owns
    it, on top of its own policy. With several owners, deny wins: a member of
    two groups is refused a tool either group denies, withdraws or pins.
    """
    return owners_by_member().get(server_id, ())


def member_policy_scopes(server_id: str, owning_groups: tuple[str, ...]) -> list[tuple[str, str | None, str | None]]:
    """Every ``(server id, group id, member server id)`` a call naming *server_id* is resolved under.

    The server's own scope, and one scope per group that owns it, asked as a
    call naming that group and routed to this member is asked. A tool is
    allowed only when every scope allows it. The front door asks these same
    scopes for a member of several groups, which it routes to the member.
    """
    return [(server_id, None, None), *((group_id, group_id, server_id) for group_id in owning_groups)]


def _withdrawn_in_scope(
    proj_registry: Any, projection: Any, tool: str, tenant_id: str | None, owning_groups: tuple[str, ...]
) -> bool:
    """Whether *tool* is withdrawn for *tenant_id* on this call (#231).

    *projection* is the tool's projection as the call resolved it. It already
    folds in every withdrawal declared on the id the call named, or on the
    member a group selected. ``None`` means the catalogue does not know the
    tool, and that does not block. A member named directly is also withdrawn
    by a withdrawal on any group that owns it.

    The withdrawal gate and the re-check after an approval hold both ask this,
    so they cannot ask different questions.
    """
    if projection is not None and projection.is_withdrawn_for(tenant_id):
        return True
    return any(proj_registry.is_withdrawn(group_id, tool, tenant_id=tenant_id) for group_id in owning_groups)


#: What a call refused by tool access is told. A task's follow-up is told the same (#1473).
_TOOL_ACCESS_DENIED = "Tool not available for this mcp_server"


def _withdrawn_message(tool: str) -> str:
    """What a call of a withdrawn *tool* is told. A task's follow-up is told the same (#1473)."""
    return f"Tool '{tool}' is withdrawn for this tenant"


def _policy_scopes(
    mcp_server: str, is_group: bool, target_server_id: str, owning_groups: tuple[str, ...]
) -> list[tuple[str, str | None, str | None]]:
    """Every ``(server id, group id, member server id)`` a call's policy is resolved under.

    - A call naming a group has one scope: the group, plus the member
      ``_gate_resolve_target`` selected. The member's policy is keyed by
      its SERVER id (#1164).
    - A call naming a server has its own scope. When the server is a group
      member, there is also one scope per owning group, asked as a call
      naming that group and routed to this member is asked.

    The access gate, the approval gate, the re-check after a hold and a
    task's follow-ups ask these same scopes, each with the caller's tenant.
    The approval gate used to ask only the named server, with no tenant.
    """
    if is_group:
        return [(mcp_server, mcp_server, target_server_id or None)]
    return member_policy_scopes(mcp_server, owning_groups)


def _allowed_in_every_scope(
    resolver: Any, tool: str, tenant_id: str | None, scopes: list[tuple[str, str | None, str | None]]
) -> bool:
    """Whether every scope's policy allows *tool* for *tenant_id*. Deny wins."""
    return all(
        resolver.is_tool_allowed(
            mcp_server_id=server_id,
            tool_name=tool,
            group_id=group_id,
            member_id=tenant_id,
            member_server_id=member_server_id,
        )
        for server_id, group_id, member_server_id in scopes
    )


def _resolve_projection(
    proj_registry: Any, mcp_server: str, tool: str, tenant_id: str | None, target_server_id: str
) -> Any:
    """The tool's projection under the id a call named, else under the member it went to (#1040)."""
    resolved = proj_registry.resolve(mcp_server, tool, tenant_id)
    if resolved is None and target_server_id and target_server_id != mcp_server:
        resolved = proj_registry.resolve(target_server_id, tool, tenant_id)
    return resolved


def _approval_policy(
    resolver: Any, tool: str, tenant_id: str | None, scopes: list[tuple[str, str | None, str | None]]
) -> Any:
    """The effective policy of the first of *scopes* whose approval list holds *tool*, or None.

    Every scope the access gate asks, each with the caller's tenant. A tool on
    any of their approval lists needs approval, and the first scope that asks
    supplies the timeout and channel. There is no `_global` second lookup:
    `_compute_effective_policy` merges `_global` into every scope it resolves.
    """
    for server_id, group_id, member_server_id in scopes:
        scoped = resolver.resolve_effective_policy(server_id, group_id, tenant_id, member_server_id=member_server_id)
        if not scoped.is_unrestricted() and scoped.requires_approval(tool):
            return scoped
    return None


@dataclass(frozen=True)
class _Governance:
    """What governs one call, decided against one configuration's overlays (#1431).

    The access, withdrawal, pin and approval gates each act on their part of
    it. They used to read the overlays themselves, one gate after another, so a
    reload that landed between two gates could combine two files. A reload
    that moves a control from `tools.deny_list: [t]` to
    `tool_projection.withdrawn: [t]` let `t` through on the new policy and the
    previous withdrawals, which neither file allows. `_decide_governance`
    makes the whole decision through `read_as_one_set` before a gate acts on
    any of it. The groups that own a member named directly are read in it too
    (#1488): a reload swaps the membership with the overlays, and a member
    moved from one group to another was otherwise governed by the group it
    left, under that group's new policy.
    """

    #: The tenant every overlay was asked for: the caller's.
    tenant_id: str | None
    #: The groups that own the server the call names, read with the overlays.
    #: Empty for a call that names a group, and for a server in no group.
    owning_groups: tuple[str, ...]
    #: Every scope the call's policy is resolved under (`_policy_scopes`), under `owning_groups`.
    scopes: tuple[tuple[str, str | None, str | None], ...]
    #: Whether the policy of every scope `_policy_scopes` names allows the tool. Deny wins.
    allowed: bool
    #: The tool's projection as the call resolves it (`_resolve_projection`). None: not in the catalogue.
    projection: Any
    #: Whether the tool is withdrawn for the tenant (`_withdrawn_in_scope`).
    withdrawn: bool
    #: ``(id, pin, mode)`` for each pin the call must match: the pins of the
    #: groups of a member named directly first, the call's own last. Each
    #: carries the id that declared it and that id's digest-enforcement mode.
    pins: tuple[tuple[str, Any, DigestEnforcement], ...]
    #: The policy that routes the tool to a human (`_approval_policy`), or None.
    approval_policy: Any
    #: The L7 egress policy of the server the call is ROUTED to, which the
    #: approval gate routes the call on (`BatchExecutor._l7_approval_rule`).
    #: For a call naming a group, the member `_gate_resolve_target` selected
    #: (#1499). None: that server has none, or it was not looked up.
    l7_policy: Any = None


def _l7_policy_of(servers: Any, mcp_server_id: str) -> Any:
    """The L7 egress policy of the server *servers* holds under *mcp_server_id*, or None."""
    try:
        server = servers.get(mcp_server_id)
    except Exception:  # noqa: BLE001 -- resolution problems belong to the invoke path's own errors
        return None
    return getattr(server, "l7_policy", None)


def _decide_governance(
    resolver: Any,
    proj_registry: Any,
    mcp_server: str,
    tool: str,
    tenant_id: str | None,
    *,
    is_group: bool,
    target_server_id: str,
    servers: Any = None,
) -> _Governance:
    """Decide what governs a call of *tool* on *mcp_server* for *tenant_id*. Run it through `read_as_one_set`.

    It reads the groups that own a member named directly, the policies, the
    withdrawals, the pins and their modes, and the L7 egress policy, and
    changes nothing, so it can be made again when a reload swaps them while it
    runs.

    Args:
        mcp_server: The id the call named: a group or a server.
        is_group: Whether that id names a group, as `_gate_resolve_target` found it.
        target_server_id: The server the call goes to. For a group, the member it selected.
        servers: The server repository the L7 egress policy of the call's
            target server is read from. None: it is not read.
    """
    # Which groups own a member named directly, read with the overlays that
    # govern it: never the previous file's groups with the new file's
    # policies (#1488).
    owning_groups = () if is_group else _groups_owning(mcp_server)
    scopes = _policy_scopes(mcp_server, is_group, target_server_id, owning_groups)
    projection = _resolve_projection(proj_registry, mcp_server, tool, tenant_id, target_server_id)
    own = proj_registry.resolve_pin(mcp_server, tool, tenant_id)
    if own is None and target_server_id and target_server_id != mcp_server:
        # A pin declared on the member a group selected. Same two-name problem
        # as the projection (#1040): without this, a pinned tool served through
        # a group was never validated against its pin, in either topology and
        # with no listing filter behind it.
        own = proj_registry.resolve_pin(target_server_id, tool, tenant_id)
    # A group member named directly: the pins its groups declare apply as
    # well. Before this, a pin on the group did not hold against a call naming
    # its member.
    pins = [
        (group_id, pin)
        for group_id in owning_groups
        if (pin := proj_registry.resolve_pin(group_id, tool, tenant_id)) is not None
    ]
    if own is not None:
        pins.append((mcp_server, own))
    return _Governance(
        tenant_id=tenant_id,
        owning_groups=owning_groups,
        scopes=tuple(scopes),
        allowed=_allowed_in_every_scope(resolver, tool, tenant_id, scopes),
        projection=projection,
        withdrawn=_withdrawn_in_scope(proj_registry, projection, tool, tenant_id, owning_groups),
        pins=tuple((scope, pin, proj_registry.digest_enforcement(scope)) for scope, pin in pins),
        approval_policy=_approval_policy(resolver, tool, tenant_id, scopes),
        # The L7 policy of the server the call is ROUTED to, not of the id it
        # names. A group id is not a server id, so a call naming a group read
        # no policy at all: the member's `requireApproval` rule never reached
        # a human, and the member's own L7 check then refused the call on
        # invoke -- the operator configured "ask" and got "refuse" (#1499).
        # `_gate_resolve_target` has already selected the member, so reading
        # its policy here selects nothing and the decision stays
        # side-effect-free under `read_as_one_set`.
        l7_policy=_l7_policy_of(servers, target_server_id if is_group and target_server_id else mcp_server),
    )


@dataclass(frozen=True)
class _AfterHold:
    """What the re-check after an approval hold reads, against one configuration's overlays (#1431)."""

    #: ``(reason, error type)`` when the policy no longer allows the call, or could not be read.
    policy_refusal: tuple[str, str] | None
    projection: Any = None
    withdrawn: bool = False


def _governance_after_hold(
    resolver: Any,
    proj_registry: Any,
    call: CallSpec,
    tenant_id: str | None,
    *,
    group_id: str | None,
    target_server_id: str,
) -> _AfterHold:
    """Read the policy, the catalogue and the withdrawals after an approval hold. Run it through `read_as_one_set`.

    See `BatchExecutor._revalidate_after_hold` for the arguments.
    """
    # The groups that own a member named directly, as they are now: read with
    # the policies and withdrawals below, as the gate before the hold read
    # them (#1488). A call naming a group has none.
    owning_groups = () if group_id is not None else _groups_owning(call.mcp_server)
    # The caller's tenant and the target group are carried, not dropped: asked
    # without them this was a different question than the pre-hold gate asked,
    # and in front_door a resolve with no member_id is the fail-closed
    # missing-identity branch, which refused EVERY approved call at dispatch
    # (#1039). Then the groups of a member named directly, asked exactly as
    # the gate before the hold asked them.
    #
    # No `_global` second lookup: `_compute_effective_policy` merges the
    # `_global` policy into every scope it resolves, so a result that is
    # unrestricted means `_global` was empty too.
    scopes = [
        (call.mcp_server, group_id, target_server_id or None),
        *((owner, owner, call.mcp_server) for owner in owning_groups),
    ]
    try:
        denied = any(
            not policy.is_unrestricted() and not policy.is_tool_allowed(call.tool)
            for policy in (
                resolver.resolve_effective_policy(server_id, scope_group, tenant_id, member_server_id=member)
                for server_id, scope_group, member in scopes
            )
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed on an unreadable policy
        return _AfterHold((f"policy could not be re-resolved: {exc}", "ApprovalRevalidationError"))
    if denied:
        return _AfterHold(("tool is no longer allowed by policy", "ToolAccessDenied"))
    # The catalogue as it is now. The withdrawal and pin re-checks both read it.
    projection = _resolve_projection(proj_registry, call.mcp_server, call.tool, tenant_id, target_server_id)
    return _AfterHold(
        None, projection, _withdrawn_in_scope(proj_registry, projection, call.tool, tenant_id, owning_groups)
    )


def current_tool_access_refusal(
    mcp_server: str, tool: str, tenant_id: str | None, *, target_server_id: str = ""
) -> tuple[str, str] | None:
    """What a new call of *tool* on *mcp_server* would be refused with now, by tool access or withdrawal.

    A relayed task's follow-ups ask this, so a task and a call of its tool get
    one answer (#1473). The questions are the access and withdrawal gates':
    the same scopes, resolver, projection lookup and tenant, in the same
    order, and the same refusal.

    Args:
        mcp_server: The id the call that created the task named: a group or a server.
        tool: The tool that call named.
        tenant_id: The caller's tenant.
        target_server_id: The server the call went to. For a group, the member it selected.

    Returns:
        ``(message, error_type)`` as the call's refusal carries them, or ``None``
        when a call would pass both gates.
    """
    resolver, registry = get_tool_access_resolver(), get_tool_projection_registry()

    def decide() -> _Governance:
        # A group id is not a server id, so this is the choice `_gate_resolve_target` makes.
        is_group = mcp_server in GROUPS
        return _decide_governance(
            resolver, registry, mcp_server, tool, tenant_id, is_group=is_group, target_server_id=target_server_id
        )

    # The gates' one decision, against one configuration's overlays and groups (#1431, #1488).
    governance = read_as_one_set(decide)
    if not governance.allowed:
        return _TOOL_ACCESS_DENIED, "ToolAccessDeniedError"
    if governance.withdrawn:
        return _withdrawn_message(tool), "ToolWithdrawnError"
    return None


@dataclass
class _CallPipeline:
    """Mutable state threaded through the gates of a single batch call.

    A shared object rather than a widening parameter list: the later gates need
    what the earlier ones resolved -- the selected group member, the tool
    projection, the tenant's digest pin -- and passing eleven values down a
    chain of eleven methods is how the 454-line function this replaced came to
    be one function.
    """

    call: CallSpec
    ctx: Any
    call_start: float
    cancel_event: threading.Event
    global_timeout: float
    batch_start_time: float
    caller_tenant_id: str | None
    resolver: Any
    proj_registry: Any
    tracer: Any

    #: Set by _gate_global_timeout: what is left of the batch budget.
    effective_timeout: float = 0.0
    #: Set by _gate_resolve_target.
    mcp_server_obj: Any = None
    is_group: bool = False
    group_obj: Any = None
    target_server_id: str = ""
    #: Decided when a gate first asks: see `governance`.
    _governance: _Governance | None = None
    _projection: Any = _UNRESOLVED
    #: True when a pin exists but the catalogue was not there to check it
    #: against; the cold start populates it and the gate re-runs (#601).
    digest_pin_deferred: bool = False
    #: Set by _gate_tenant_budget: the token taken from the caller's tenant
    #: budget, given back if a later gate refuses the call (#1445).
    reservation: Reservation | None = None

    @property
    def governance(self) -> _Governance:
        """What governs this call: one decision, against one configuration's overlays (#1431).

        Made when a gate first asks, after `_gate_resolve_target` chose the
        target, and kept. The access, withdrawal, pin and approval gates each
        act on their part of it, so a reload that lands between two gates
        cannot give them two different files. The groups that own a server
        named directly are read in it, with the overlays (#1488).
        """
        if self._governance is None:
            self._governance = read_as_one_set(
                partial(
                    _decide_governance,
                    self.resolver,
                    self.proj_registry,
                    self.call.mcp_server,
                    self.call.tool,
                    self.caller_tenant_id,
                    is_group=self.is_group,
                    target_server_id=self.target_server_id,
                    servers=getattr(self.ctx, "repository", None),
                )
            )
        return self._governance

    @property
    def owning_groups(self) -> tuple[str, ...]:
        """The groups a server named directly is a member of, as the governance decision read them.

        Empty for a call that names a group, and for a server that is in no group.
        """
        return self.governance.owning_groups

    @property
    def projection(self) -> Any:
        """The tool's projection, as the governance decision resolved it.

        Part of the decision rather than a field set by whichever gate happens
        to run first: both the withdrawal gate and the digest-pin gate need it,
        and making one of them responsible for populating it for the other is
        an ordering dependency that fails silently -- reorder the two and the
        pin check quietly defers instead of running.

        Two ids, because a group has two names (#1040). ``call.mcp_server`` is
        the GROUP id whenever a group is the target -- front_door collapses the
        member so selection stays with the group's strategy, and an egress caller
        names the group directly -- while the registry is keyed by the id that
        STARTED, which is always a member. Resolving only the group id returned
        ``None``, and ``None`` means "unknown tool, do not block": the withdrawal
        gate waved every group-routed call through and the pin gate returned
        before checking anything. The group id is asked first because a
        group-declared withdrawal is the narrower statement (it covers the group
        however a member is selected); the selected member answers otherwise, and
        is also where the discovered schema the pin is validated against lives.
        """
        if self._projection is _UNRESOLVED:
            self._projection = self.governance.projection
        return self._projection

    def reresolve_projection(self) -> Any:
        """Look the projection up again, after a cold start populated it.

        The deferred pin gate needs the answer to a question that had none when
        the decision was made. It asks `_resolve_projection`, the lookup the
        decision makes, rather than the registry itself, because a second copy
        of the two-name lookup is a copy that can be missing the fallback --
        which is what it was: the deferred gate asked the group id alone, found
        nothing for a member that had just started, and refused the first
        pinned call after every gateway boot as unverifiable (#1166).
        """
        self._projection = _resolve_projection(
            self.proj_registry, self.call.mcp_server, self.call.tool, self.caller_tenant_id, self.target_server_id
        )
        return self._projection

    def pins(self) -> tuple[tuple[str, Any, DigestEnforcement], ...]:
        """Every pin this call must match: ``(id that declared it, pin, that id's enforcement mode)``.

        The groups' pins come first and the call's own pin last. When every pin
        matches, the digest bound to the request is then the call's own, as it
        was before group pins were added.
        """
        return self.governance.pins

    def policy_scopes(self) -> list[tuple[str, str | None, str | None]]:
        """Every ``(server id, group id, member server id)`` this call's policy is resolved under.

        See :func:`_policy_scopes`. The scopes the governance decision was made under.
        """
        return list(self.governance.scopes)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.call_start) * 1000

    def refuse(self, error: str, error_type: str) -> CallResult:
        """A refusal for this call, timed from its start.

        Every gate built this by hand, eight lines apiece, and the elapsed_ms
        argument is the one an edit forgets.
        """
        return CallResult(
            index=self.call.index,
            call_id=self.call.call_id,
            success=False,
            error=error,
            error_type=error_type,
            elapsed_ms=self.elapsed_ms(),
        )


def _retry_policy_for(call: Any) -> RetryPolicy | None:
    """The policy this call retries under, or ``None`` for a single attempt.

    The `retry:` section had a loader, a log line confirming what it loaded, and
    no consumer: the executor built `RetryPolicy(max_attempts=call.max_retries)`
    from the `hangar_batch` argument alone, so `backoff`, `initial_delay`,
    `max_delay`, `retry_on` and `jitter*` were always the class defaults and
    `per_mcp_server` applied to nothing (#1162).

    Two rules decide the result:

    * **Config is the ceiling.** An explicit `max_attempts` from the caller can
      lower the attempt count and never raise it. The operator configures how
      hard this gateway is willing to lean on an upstream; a caller asking for
      more attempts than that is asking to be someone else's load problem.
    * **No config means no change.** `RetryPolicy()` is three attempts, so
      treating "nothing configured" as a default policy would turn every batch
      call in every deployment into three -- which is why the store answers
      `None` rather than a policy nobody asked for.
    """
    configured = configured_retry_policy(call.mcp_server)
    caller_attempts = getattr(call, "max_retries", 1) or 1

    if configured is None:
        return RetryPolicy(max_attempts=caller_attempts) if caller_attempts > 1 else None

    attempts = min(configured.max_attempts, caller_attempts) if caller_attempts > 1 else configured.max_attempts
    if attempts <= 1:
        return None
    return replace(configured, max_attempts=attempts)


def _log_call_failure(call: Any, error: Any, error_type: str, elapsed_ms: float) -> None:
    """Log a failed call, loudly when the failure was a deliberate refusal.

    A policy refusal and an upstream blowing up are not the same class of event
    and were logged the same way: `logger.debug`, which a default deployment
    does not emit, carrying `str(e)` -- the generic caller-facing message, with
    the reason the policy computed left behind in `.details` where only the REST
    middleware ever looked (#1128).

    The refusal is an enforcement decision this gateway made on purpose, so it
    is a warning and it says why. Everything else keeps the debug level: an
    upstream failure is already reported to the caller in the CallResult, and
    raising it here would make a batch of failing calls a log flood.
    """
    details = getattr(error, "details", None)
    reason = details.get("reason") if isinstance(details, dict) else None
    refused = error_type in ("EgressPolicyDeniedError", "EgressPolicyApprovalRequiredError")
    log = logger.warning if refused else logger.debug
    log(
        "batch_call_refused" if refused else "batch_call_failed",
        call_id=call.call_id,
        mcp_server=call.mcp_server,
        tool=call.tool,
        error=str(error),
        error_type=error_type,
        reason=reason,
        policy_id=getattr(error, "policy_id", None),
        elapsed_ms=round(elapsed_ms, 2),
    )


class BatchExecutor:
    """Executes batch invocations with parallel processing.

    Uses a two-level concurrency model:
    1. ThreadPoolExecutor(max_workers=N) provides per-batch thread management.
       N is the effective batch concurrency: min(user_param, global_limit).
    2. ConcurrencyManager provides cross-batch, system-wide concurrency control
       via global and per-mcp_server semaphores.

    All calls in a batch are submitted to the thread pool at once. Each worker
    thread acquires global + mcp_server semaphores before executing, providing
    backpressure without sequential chunking. Fast calls release their slots
    immediately, allowing queued calls to proceed without waiting for the
    entire batch wave to complete.
    """

    def __init__(
        self,
        concurrency_manager: ConcurrencyManager | None = None,
        validator_pipeline: ValidatorPipeline | None = None,
        mutator_pipeline: MutatorPipeline | None = None,
    ):
        self._single_flight = SingleFlight(cache_results=False)
        self._active_batches = 0
        self._active_lock = threading.Lock()
        self._concurrency_manager = concurrency_manager
        # Interceptor validator pipeline. Defaults to a fresh EMPTY pipeline
        # (no validators registered), so it always allows -- preserving current
        # behavior. Fail-closed only takes effect once validators are registered.
        self._validator_pipeline = validator_pipeline if validator_pipeline is not None else ValidatorPipeline()
        # Interceptor mutator pipeline. Defaults to a fresh EMPTY pipeline (no
        # mutators registered), so payloads pass through unchanged -- preserving
        # current behavior. Transforms only take effect once mutators are registered.
        self._mutator_pipeline = mutator_pipeline if mutator_pipeline is not None else MutatorPipeline()

    @property
    def concurrency_manager(self) -> ConcurrencyManager:
        """Get the concurrency manager (lazy-loaded from singleton if not injected)."""
        if self._concurrency_manager is None:
            self._concurrency_manager = get_concurrency_manager()
        return self._concurrency_manager

    def _apply_batch_truncation(
        self, batch_id: str, results: list[CallResult], whole: frozenset[int] = frozenset()
    ) -> list[CallResult]:
        """Apply batch-level truncation if enabled and needed.

        Args:
            batch_id: The batch identifier.
            results: List of call results to potentially truncate.
            whole: Indexes of the calls whose caller takes the whole result
                (``CallSpec.whole_result``, #1453). They are left out of the
                batch budget: never cut, and no continuation is stored for them.

        Returns:
            List of results, potentially with some truncated.
        """
        from ...bootstrap.truncation import get_truncation_manager

        truncation_manager = get_truncation_manager()
        if truncation_manager is None:
            return results

        # Each continuation is cached for the caller this batch runs for, and
        # answers no one else. This runs on the calling
        # thread, under the identity hangar_call bound for the batch, and the
        # continuation tools read the caller the same way.
        owner = ContinuationOwner.of(get_identity_context())
        cut = truncation_manager.process_batch(batch_id, [r for r in results if r.index not in whole], owner=owner)
        by_index = {r.index: r for r in cut}
        return [by_index.get(r.index, r) for r in results]

    def _l7_approval_rule(self, call: CallSpec, ctx: Any, *, policy: Any = _UNRESOLVED) -> str | None:
        """The L7 (MCPEgressPolicy) requireApproval verdict for this call.

        Returns the human-readable reason when the target server's enforced L7
        policy routes this tool to approval (#921), else None. Audit mode
        observes and never blocks, so it never asks a human; deny needs no
        gate -- the aggregate refuses it on invoke.

        Args:
            policy: The L7 policy the call's governance decision read, with its
                overlays and groups (#1488). Without it, the policy of the
                server the call names is read now.
        """
        if policy is _UNRESOLVED:
            policy = _l7_policy_of(getattr(ctx, "repository", None), call.mcp_server)
        if policy is None:
            return None

        from mcp_hangar.context import get_routing_headers
        from mcp_hangar.domain.policies.egress_l7 import PolicyMode, ToolAction, evaluate

        if policy.mode is not PolicyMode.ENFORCE:
            return None
        # Same inputs as the aggregate's own evaluation (#1058): a header
        # selector that routes a call to approval there must route it here, or
        # the gate is skipped and the aggregate refuses instead of asking.
        decision = evaluate(call.tool, call.arguments or {}, policy, get_routing_headers())
        if decision.action is ToolAction.REQUIRE_APPROVAL:
            return "; ".join(decision.reasons) or "matched a requireApproval rule"
        return None

    def _check_approval_gate(
        self,
        call: CallSpec,
        resolver: Any,
        ctx: Any,
        *,
        tenant_id: str | None = None,
        scopes: list[tuple[str, str | None, str | None]] | None = None,
        governance: _Governance | None = None,
    ) -> CallResult | None:
        """Check if the tool requires approval and block until resolved.

        Returns None if no approval is needed (continue execution).
        Returns a CallResult if the tool was denied or timed out.

        Args:
            tenant_id: The caller's tenant. Its own approval list applies. In
                front_door, a resolve without it is the fail-closed
                missing-identity branch, which answers deny-all and so never
                asks for approval (#1039).
            scopes: The ``(server, group, member server)`` scopes the access
                gate asked, from ``_CallPipeline.policy_scopes``. The default
                is the named server alone.
            governance: The call's governance decision. Its approval policy
                is the one this gate applies, read with the rest of the call's
                governance as one set (#1431). Without it, the approval lists
                of *scopes* are read here, as one set.
        """
        # Cleared per call: worker threads are reused across calls, so a stale
        # id from the previous call in this thread must never be revalidated
        # against the current one.
        _approval_loop_local.approval_id = None

        # The approval lists of every scope the access gate asked, each with
        # the caller's tenant: see `_approval_policy`. Before this, only the
        # named server's list was read. A group's list and a tenant's list
        # never held a call, and in front_door no list did.
        policy: Any
        if governance is not None:
            policy = governance.approval_policy
        else:
            policy = read_as_one_set(
                partial(_approval_policy, resolver, call.tool, tenant_id, scopes or [(call.mcp_server, None, None)])
            )

        needs_mrtr_approval = policy is not None

        # The L7 egress policy is the second, independent source of "ask a
        # human" (#921): before this, its requireApproval verdict failed
        # closed in the aggregate and was indistinguishable from deny.
        l7_rule = (
            self._l7_approval_rule(call, ctx)
            if governance is None
            else self._l7_approval_rule(call, ctx, policy=governance.l7_policy)
        )

        if not needs_mrtr_approval and l7_rule is None:
            return None

        # Tool requires approval -- delegate to ApprovalGateService
        gate_service = getattr(ctx, "approval_gate", None)
        if gate_service is None:
            if l7_rule is not None:
                # An L7 requireApproval with nobody to ask stays fail-closed:
                # the aggregate raises EgressPolicyApprovalRequiredError on
                # invoke, exactly as before this wiring. Do NOT return a pass.
                logger.info("approval_gate_not_configured_l7_fails_closed", tool=call.tool, rule=l7_rule)
                return None
            logger.debug("approval_gate_not_configured", tool=call.tool)
            return None

        if not needs_mrtr_approval:
            # L7-only: hand the gate a policy that says exactly what the
            # egress policy said -- this one tool needs a human. Timeout and
            # channel fall back to the deployment defaults the gate already
            # applies for an empty channel.
            from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

            policy = ToolAccessPolicy(approval_list=(call.tool,))
            logger.info("egress_policy_approval_routing", tool=call.tool, rule=l7_rule)

        logger.info(
            "approval_gate_blocking",
            mcp_server=call.mcp_server,
            tool=call.tool,
            call_id=call.call_id,
        )

        try:
            # ApprovalGateService.check() is async; we run it on an event loop
            # of its own, closed when the wait ends. We cannot use the main
            # FastMCP loop because hangar_call() blocks it. See
            # _run_approval_coroutine() for the cross-loop signaling rationale.
            # Bind the caller's tenant and identity onto the approval so the
            # resolve/list surfaces can be scoped to them. Without this, an
            # approver in one tenant can see and resolve another tenant's
            # approvals, because authorization is by permission alone.
            _ident = get_identity_context()
            _caller = _ident.caller if _ident is not None else None
            _tenant_id = _caller.tenant_id if _caller is not None else None
            _requested_by = (_caller.user_id or _caller.agent_id) if _caller is not None else None

            result = _run_approval_coroutine(
                gate_service.check(
                    mcp_server_id=call.mcp_server,
                    tool_name=call.tool,
                    arguments=call.arguments,
                    policy=policy,
                    correlation_id=call.call_id,
                    tenant_id=_tenant_id,
                    requested_by=_requested_by,
                )
            )
        except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
            logger.warning("approval_gate_error", tool=call.tool, error=str(exc))
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=f"Approval gate error: {exc}",
                error_type="ApprovalGateError",
                elapsed_ms=0,
            )

        if result.approved and result.approval_id is None:
            # not_required -- no approval was needed after detailed check
            _approval_loop_local.approval_id = None
            return None

        if not result.approved:
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=result.reason or "Tool execution denied by approval gate",
                error_type=result.error_code or "ApprovalDenied",
                elapsed_ms=0,
            )

        # Approved -- continue execution. The caller revalidates before dispatch;
        # see _revalidate_approval.
        _approval_loop_local.approval_id = result.approval_id
        return None

    def _revalidate_after_hold(
        self,
        call: CallSpec,
        resolver: Any,
        ctx: Any,
        approval_id: str,
        pin: Any,
        proj_registry: Any,
        caller_tenant_id: Any,
        enforce_digest_pin: Any,
        *,
        group_id: str | None = None,
        target_server_id: str = "",
    ) -> CallResult | None:
        """Re-check, after an approval hold, everything decided before it.

        Returns a refusal ``CallResult`` when the approved call may no longer
        run, or ``None`` to proceed.

        Args:
            group_id: The group the call targets, if any -- the same value
                ``_gate_tool_access`` passes. Without it (#1039) this asked the
                resolver a different question than the pre-hold gate did: a
                group's policy was never merged, so a deny added to a group
                during the hold did not refuse the approved call.
            target_server_id: The member a group selected, for the projection
                and pin re-resolve (#1040).

        A member named directly is re-checked against the groups that own it
        now, read with the policy and withdrawals (#1488), as
        ``_gate_tool_access`` asked the groups that owned it then. A deny added
        to one of them during the hold refuses the approved call.
        """

        def _refuse(reason: str, code: str) -> CallResult:
            logger.warning(
                "approval_revalidation_failed",
                approval_id=approval_id,
                mcp_server=call.mcp_server,
                tool=call.tool,
                reason=reason,
            )
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=f"Approval no longer valid at dispatch: {reason}",
                error_type=code,
                elapsed_ms=0,
            )

        # The record itself: still approved, still inside its window, and still
        # describing these arguments.
        gate_service = getattr(ctx, "approval_gate", None)
        if gate_service is not None and hasattr(gate_service, "revalidate"):
            try:
                reason = _run_approval_coroutine(gate_service.revalidate(approval_id, call.arguments or {}))
            except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
                # Fail closed: an approval we cannot re-verify is not an
                # approval we can act on.
                return _refuse(f"revalidation error: {exc}", "ApprovalRevalidationError")
            if reason is not None:
                return _refuse(reason, "ApprovalNoLongerValid")

        # The effective policy, the catalogue and the withdrawals as they are
        # now, read against one configuration's overlays, never a mix of two
        # (#1431). Acted on in the order the gates before the hold act on them.
        now = read_as_one_set(
            partial(
                _governance_after_hold,
                resolver,
                proj_registry,
                call,
                caller_tenant_id,
                group_id=group_id,
                target_server_id=target_server_id,
            )
        )

        # Effective policy, re-resolved. A tool moved to deny during the hold
        # must not execute on the pre-change decision.
        if now.policy_refusal is not None:
            return _refuse(*now.policy_refusal)

        # Withdrawal, re-checked with the scopes `_gate_withdrawal` uses and
        # refused with the outcome it gives. A tool withdrawn while the call
        # waited for a human used to run once approved, although this re-check
        # was documented to cover it.
        if now.withdrawn:
            logger.warning(
                "approval_revalidation_failed",
                approval_id=approval_id,
                mcp_server=call.mcp_server,
                tool=call.tool,
                reason="tool withdrawn during the hold",
            )
            return self._withdrawn_refusal(call, ctx, caller_tenant_id, 0.0)

        # The pinned tool digest, re-verified against the catalogue as it is
        # now. The pre-gate check spoke for a schema that may since have moved.
        # The pins, and the mode each is enforced in, are the ones the call
        # was approved under: one decision, made before the hold.
        if pin is not None and now.projection is not None:
            rejection: CallResult | None = enforce_digest_pin(now.projection, pin)
            if rejection is not None:
                return rejection

        return None

    def _check_validators(self, call: CallSpec) -> CallResult | None:
        """Run the interceptor ValidatorPipeline against this tool call.

        Fail-closed but behavior-preserving: with the default empty pipeline no
        validators run, so this always returns None (proceed). Once validators
        are registered, an enforced denial short-circuits the call BEFORE the
        approval gate and invoke.

        Returns None if the call is allowed (continue execution). Returns a
        CallResult if a validator denied the call.
        """
        ctx = ValidationContext(
            method="tools/call",
            direction="request",
            payload={"name": call.tool, "arguments": call.arguments or {}},
            correlation_id=call.call_id,
        )
        result = self._validator_pipeline.execute(ctx)
        if not result.allowed:
            return CallResult(
                index=call.index,
                call_id=call.call_id,
                success=False,
                error=result.reason or "Denied by validator",
                error_type="ValidatorDenied",
                elapsed_ms=0,
            )
        return None

    def _mutate(
        self,
        method: str,
        direction: Literal["request", "response"],
        payload: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Run the interceptor MutatorPipeline over a tool-call payload.

        Behavior-preserving: with the default empty pipeline no mutators run, so
        the payload is returned unchanged. Once mutators are registered, the
        applicable ones transform the payload in priority order and the
        (possibly changed) payload is returned.
        """
        ctx = MutationContext(
            method=method,
            direction=direction,
            payload=payload,
            correlation_id=correlation_id,
        )
        result = self._mutator_pipeline.execute(ctx)
        return result.payload

    def execute(  # noqa: C901 -- baseline CC=17; split before extending
        self,
        batch_id: str,
        calls: list[CallSpec],
        max_concurrency: int,
        global_timeout: float,
        fail_fast: bool,
        request_ctx: Any | None = None,
    ) -> BatchResult:
        """Execute batch of calls in parallel.

        All calls are submitted to the thread pool immediately. Concurrency is
        controlled by two mechanisms:
        - ThreadPoolExecutor max_workers: caps threads for this batch
        - ConcurrencyManager semaphores: caps in-flight calls globally and per-mcp_server

        The effective per-batch thread count is min(max_concurrency, global_limit)
        when the global limit is set, ensuring we don't create more threads than
        the system-wide limit allows.

        Args:
            batch_id: Unique batch identifier.
            calls: List of call specifications.
            max_concurrency: Maximum parallel workers for this batch.
            global_timeout: Global timeout for entire batch.
            fail_fast: Abort on first error if True.
            request_ctx: The real FastMCP request ``Context`` (when invoked over an
                MCP transport), used solely to read the inbound ``params._meta``
                for trace context and protocol negotiation. ``None`` on the
                stdio / no-request path, in which case both default (empty trace /
                supported protocol version) exactly as before. Distinct from the
                ApplicationContext returned by ``get_context()``, which has no
                ``request_context`` and is still used for the event/command buses.

        Returns:
            BatchResult with all call results.
        """
        ctx = get_context()

        start_time = time.perf_counter()
        cancel_event = threading.Event()
        results: list[CallResult | None] = [None] * len(calls)
        succeeded = 0
        failed = 0
        cancelled = 0

        # Determine effective thread pool size:
        # - Capped by the per-batch max_concurrency (user/default)
        # - Also capped by global concurrency limit (no point creating more
        #   threads than the global semaphore will allow through)
        cm = self.concurrency_manager
        global_limit = cm.global_limit
        if global_limit > 0:
            effective_workers = min(max_concurrency, global_limit)
        else:
            effective_workers = max_concurrency

        tracer = get_tracer(__name__)

        # Track active batches for metrics
        with self._active_lock:
            self._active_batches += 1
            BATCH_CONCURRENCY_GAUGE.set(self._active_batches)

        # Stateless negotiation (SEP-2575): the client conveys its protocolVersion
        # and capabilities per request in params._meta (no initialize handshake).
        # Read them once at ingress and publish to a request-scoped contextvar that
        # batch worker threads inherit via copy_context(). Additive: no gating here.
        # Over streamable-HTTP the inbound _meta lives on the FastMCP request_ctx
        # (the ApplicationContext has no request_context), so read from request_ctx;
        # when it is None (stdio / no request) the helper yields None and negotiation
        # falls back to the default supported version -- unchanged behavior.
        negotiation_token = set_current_protocol_negotiation(read_protocol_negotiation(_inbound_meta_dict(request_ctx)))

        # The same request's SEP-2243 routing headers, for an L7 policy that
        # selects on Mcp-Param-* (#1058). Bound here rather than only on the
        # front door so a selector is never silently inert on this surface --
        # a policy that reports enforcing while a rule cannot fire is the
        # failure this module already refuses for secret-pattern groups.
        routing_token = bind_routing_headers(request_ctx)

        # Both bindings are this call's, so both tokens are reset in the finally
        # below, the way the identity binding is (see hangar_call). A caller that
        # reaches execute() on a context it keeps -- rather than through the
        # asyncio.to_thread copy both surfaces used to take -- would otherwise
        # hand the next call the headers this one routed on (#1503).
        try:
            with tracer.start_as_current_span("batch.execute") as batch_span:
                batch_span.set_attribute("batch.id", batch_id)
                batch_span.set_attribute("batch.call_count", len(calls))
                batch_span.set_attribute("batch.max_concurrency", max_concurrency)
                batch_span.set_attribute("batch.timeout", global_timeout)
                batch_span.set_attribute("batch.fail_fast", fail_fast)
                batch_span.set_attribute("batch.effective_workers", effective_workers)

                # Emit batch requested event
                mcp_servers = list(set(c.mcp_server for c in calls))
                ctx.event_bus.publish(
                    BatchInvocationRequested(
                        batch_id=batch_id,
                        call_count=len(calls),
                        mcp_servers=mcp_servers,
                        max_concurrency=max_concurrency,
                        timeout=global_timeout,
                        fail_fast=fail_fast,
                    )
                )

                logger.debug(
                    "batch_dispatch_start",
                    batch_id=batch_id,
                    call_count=len(calls),
                    effective_workers=effective_workers,
                    global_limit=global_limit if global_limit > 0 else "unlimited",
                    mcp_server_count=len(mcp_servers),
                )

                # Execute calls in thread pool — all submitted at once, semaphores
                # provide backpressure (not sequential chunking).
                # copy_context() snapshots the calling thread's contextvars
                # (identity_context_var, OTel trace context, structlog ctx, …)
                # so each worker inherits the per-request context rather than
                # getting the default empty context that ThreadPoolExecutor
                # would otherwise provide.
                # IMPORTANT: each call gets its own copy — a Context object
                # cannot be entered by more than one thread simultaneously.
                with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                    futures = {
                        executor.submit(
                            contextvars.copy_context().run,
                            self._execute_call,
                            call,
                            cancel_event,
                            global_timeout,
                            start_time,
                            request_ctx,
                        ): call.index
                        for call in calls
                    }

                    try:
                        for future in as_completed(futures, timeout=global_timeout):
                            index = futures[future]
                            try:
                                result = future.result()
                                results[index] = result

                                # Emit per-call event
                                ctx.event_bus.publish(
                                    BatchCallCompleted(
                                        batch_id=batch_id,
                                        call_id=result.call_id,
                                        call_index=result.index,
                                        mcp_server_id=calls[index].mcp_server,
                                        tool_name=calls[index].tool,
                                        success=result.success,
                                        elapsed_ms=result.elapsed_ms,
                                        error_type=result.error_type,
                                    )
                                )

                                if result.success:
                                    succeeded += 1
                                else:
                                    failed += 1
                                    if fail_fast:
                                        logger.debug(
                                            "batch_fail_fast_triggered",
                                            batch_id=batch_id,
                                            failed_index=index,
                                        )
                                        cancel_event.set()
                                        BATCH_CANCELLATIONS_TOTAL.inc(reason="fail_fast")
                                        break

                            except Exception as e:  # noqa: BLE001 -- fault-barrier: future exception handling for batch result collection
                                # Future raised exception
                                call = calls[index]
                                results[index] = CallResult(
                                    index=index,
                                    call_id=call.call_id,
                                    success=False,
                                    error=str(e),
                                    error_type=type(e).__name__,
                                    elapsed_ms=(time.perf_counter() - start_time) * 1000,
                                )
                                failed += 1

                                if fail_fast:
                                    cancel_event.set()
                                    BATCH_CANCELLATIONS_TOTAL.inc(reason="fail_fast")
                                    break

                    except TimeoutError:
                        # Global timeout exceeded
                        logger.warning(
                            "batch_global_timeout",
                            batch_id=batch_id,
                            timeout=global_timeout,
                        )
                        cancel_event.set()
                        BATCH_CANCELLATIONS_TOTAL.inc(reason="timeout")

                # After the ThreadPoolExecutor context manager exits (shutdown(wait=True)),
                # some futures may have completed after as_completed timed out (e.g.
                # approval-gated calls that were waiting for human decision).  Collect
                # those results before marking anything as cancelled.
                for future, index in futures.items():
                    if results[index] is not None:
                        continue  # already collected
                    if future.done():
                        try:
                            result = future.result(timeout=0)
                            results[index] = result
                            if result.success:
                                succeeded += 1
                            else:
                                failed += 1
                        except Exception as e:  # noqa: BLE001
                            results[index] = CallResult(
                                index=index,
                                call_id=calls[index].call_id,
                                success=False,
                                error=str(e),
                                error_type=type(e).__name__,
                                elapsed_ms=(time.perf_counter() - start_time) * 1000,
                            )
                            failed += 1

                # Fill in cancelled/timed out calls
                for i, r in enumerate(results):
                    if r is None:
                        call = calls[i]
                        results[i] = CallResult(
                            index=i,
                            call_id=call.call_id,
                            success=False,
                            error="Cancelled" if cancel_event.is_set() else "Timeout",
                            error_type="CancellationError" if cancel_event.is_set() else "TimeoutError",
                            elapsed_ms=(time.perf_counter() - start_time) * 1000,
                        )
                        cancelled += 1

                elapsed_ms = (time.perf_counter() - start_time) * 1000
                success = failed == 0 and cancelled == 0

                # Determine result status for metrics
                if success:
                    result_status = "success"
                elif succeeded > 0:
                    result_status = "partial"
                else:
                    result_status = "failure"

                # Record metrics
                BATCH_CALLS_TOTAL.inc(result=result_status)
                BATCH_SIZE_HISTOGRAM.observe(len(calls))
                BATCH_DURATION_SECONDS.observe(elapsed_ms / 1000)

                # Emit completion event
                ctx.event_bus.publish(
                    BatchInvocationCompleted(
                        batch_id=batch_id,
                        total=len(calls),
                        succeeded=succeeded,
                        failed=failed,
                        elapsed_ms=elapsed_ms,
                        cancelled=cancelled,
                    )
                )

                logger.info(
                    "batch_completed",
                    batch_id=batch_id,
                    total=len(calls),
                    succeeded=succeeded,
                    failed=failed,
                    cancelled=cancelled,
                    elapsed_ms=round(elapsed_ms, 2),
                )

                # Record batch outcome on span
                batch_span.set_attribute("batch.succeeded", succeeded)
                batch_span.set_attribute("batch.failed", failed)
                batch_span.set_attribute("batch.cancelled", cancelled)
                batch_span.set_attribute("batch.result", result_status)
                batch_span.set_attribute("batch.elapsed_ms", round(elapsed_ms, 2))

                # Apply batch-level truncation if enabled
                final_results = [r for r in results if r is not None]
                final_results = self._apply_batch_truncation(
                    batch_id, final_results, frozenset(c.index for c in calls if c.whole_result)
                )

                return BatchResult(
                    batch_id=batch_id,
                    success=success,
                    total=len(calls),
                    succeeded=succeeded,
                    failed=failed,
                    elapsed_ms=elapsed_ms,
                    results=final_results,
                    cancelled=cancelled,
                )

        finally:
            release_routing_headers(routing_token)
            reset_current_protocol_negotiation(negotiation_token)
            with self._active_lock:
                self._active_batches -= 1
                BATCH_CONCURRENCY_GAUGE.set(self._active_batches)

    def _execute_call(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        global_timeout: float,
        batch_start_time: float,
        request_ctx: Any | None = None,
    ) -> CallResult:
        """Execute a single call within the batch.

        Acquires global and per-mcp_server concurrency slots via the
        ConcurrencyManager before performing the actual invocation.
        This ensures system-wide and per-mcp_server backpressure even
        when multiple batches run concurrently.

        Handles:
        - Cooperative cancellation
        - Two-level concurrency control (global + per-mcp_server)
        - Single-flight cold starts
        - Circuit breaker checks
        - Response truncation
        - Retry with exponential backoff

        Args:
            call: Call specification.
            cancel_event: Event to check for cancellation.
            global_timeout: Global batch timeout.
            batch_start_time: When batch started (for remaining time calculation).
            request_ctx: The real FastMCP request ``Context`` (or ``None`` on the
                stdio / no-request path), used to read the inbound ``params._meta``
                for W3C trace context. Distinct from the ApplicationContext returned
                by ``get_context()``.

        Returns:
            CallResult for this call.
        """
        ctx = get_context()
        call_start = time.perf_counter()

        # Extract W3C TraceContext for distributed tracing. Per SEP-414 it travels
        # in the inbound request's params._meta (un-prefixed traceparent/tracestate);
        # fall back to the legacy call.metadata field. _meta wins when both present.
        # The inbound _meta lives on the FastMCP request_ctx (the ApplicationContext
        # has no request_context); when request_ctx is None the helper yields {} and
        # only call.metadata is used -- the pre-bridge default, unchanged.
        metadata = call.metadata or {}
        carrier_context = extract_trace_context({**metadata, **_inbound_trace_meta(request_ctx)})

        # Create a span for this batch call under its local parent. The carrier
        # only parents it when nothing local does; see _call_span_parent.
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(
            f"batch.call.{call.tool}",
            **_call_span_parent(carrier_context),
        ) as span:
            span.set_attribute("mcp.server.id", call.mcp_server)
            span.set_attribute("gen_ai.tool.name", call.tool)
            span.set_attribute("batch.call.id", call.call_id)
            result = self._execute_call_inner(
                call,
                cancel_event,
                global_timeout,
                batch_start_time,
                ctx,
                call_start,
            )
            # The inner call handles failures as data (CallResult), so the span
            # never sees an exception. Mark it ERROR explicitly so failing tool
            # calls are filterable as error traces instead of looking successful.
            # The error's class names it; its message can hold what the tool
            # returned, so it stays off the span (GHSA-qwq2-7g49-jxc6).
            if not result.success:
                mark_span_error(span, result.error_type)
            return result

    def _execute_call_inner(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        global_timeout: float,
        batch_start_time: float,
        ctx: Any,
        call_start: float,
    ) -> CallResult:
        """Inner execution logic for a single batch call (runs inside trace span).

        Separated from _execute_call so the span wraps the full call lifecycle.

        The body is a chain of gates. Each returns a ``CallResult`` to refuse the
        call or ``None`` to hand it to the next, and they share the mutable
        ``_CallPipeline`` below because the later ones need what the earlier ones
        resolved -- the selected group member, the tool projection, the tenant's
        digest pin.

        Their ORDER is load-bearing rather than incidental: the refusal a caller
        receives decides what it does next, so swapping two gates silently
        changes the answer. ``_GATES`` is that order, and
        tests/unit/test_batch_gate_precedence.py arranges pairs of them to fail
        at once and asserts which one wins.
        """
        pipeline = _CallPipeline(
            call=call,
            ctx=ctx,
            call_start=call_start,
            cancel_event=cancel_event,
            global_timeout=global_timeout,
            batch_start_time=batch_start_time,
            # Read the caller's tenant first: a group's member selection may be
            # tenant-aware (per-tenant canary / version routing, #275). The
            # identity is set by IdentityMiddleware and carried into this worker
            # thread via copy_context() (PR #239).
            caller_tenant_id=(identity.caller.tenant_id if (identity := get_identity_context()) is not None else None),
            resolver=get_tool_access_resolver(),
            proj_registry=get_tool_projection_registry(),
            tracer=get_tracer(__name__),
        )

        refusal = self._run_gates(pipeline)
        if refusal is not None:
            return refusal

        # The slot of the tenant's execution budget (#1445), taken after every
        # gate -- a call held for approval, or waiting on a cold start, holds
        # none -- and before the execution slot, so a tenant at its limit never
        # queues for one. Its token was taken by `_gate_tenant_budget`.
        admitted = self._enforce_tenant_budget(pipeline)
        if isinstance(admitted, CallResult):
            return admitted
        try:
            return self._dispatch(pipeline)
        finally:
            # On every path: a result, a relayed task handle, an exception. A
            # worker thread cannot be cancelled, so a call its batch gave up on
            # releases here too, once its invoke returns.
            admitted.release()

    def _run_gates(self, p: "_CallPipeline") -> CallResult | None:
        """Run `_GATES` in order: the first refusal, or None when every gate lets the call through.

        A token `_gate_tenant_budget` took is given back when a gate after it
        refuses the call, or raises: a call stopped there never ran.
        """
        passed = False
        try:
            for gate in _GATES:
                refusal = gate(self, p)
                if refusal is not None:
                    return refusal
            passed = True
            return None
        finally:
            if not passed and p.reservation is not None:
                p.reservation.refund()

    def _enforce_tenant_budget(self, p: "_CallPipeline") -> Grant | CallResult:
        """Take the call's slot from its tenant's budget, or refuse the call (#1445).

        Refuses at once: it never waits for a slot, and never retries. A call
        refused here after an approval hold names the approval in its log line:
        its tenant's slots were all taken when it was dispatched, and running
        it again needs a new approval. See `tenant_admission.py`.
        """
        if p.reservation is not None:
            granted = p.reservation.grant()
        else:  # not reached while `_gate_tenant_budget` is a gate: take both rather than neither
            granted = get_tenant_admission().admit(p.caller_tenant_id)
        if isinstance(granted, Grant):
            return granted
        # Read after the approval gate, which clears it for every call.
        return self._refuse_over_budget(p, granted, approval_id=getattr(_approval_loop_local, "approval_id", None))

    def _refuse_over_budget(
        self, p: "_CallPipeline", refusal: Refusal, *, approval_id: str | None = None
    ) -> CallResult:
        """Log, count and build the refusal of a call its tenant's budget does not admit."""
        # A warning when a human's approval is spent on a call that does not run.
        log = logger.warning if approval_id is not None else logger.info
        log(
            "tenant_quota_exceeded",
            mcp_server_id=p.call.mcp_server,
            tool=p.call.tool,
            tenant_id=p.caller_tenant_id,
            budget=refusal.budget,
            reason=refusal.reason,
            approval_id=approval_id,
        )
        TENANT_QUOTA_REFUSALS_TOTAL.inc(budget=refusal.budget, reason=refusal.reason)
        return p.refuse(_TENANT_QUOTA_MESSAGES[refusal.reason], "TenantQuotaExceeded")

    def _dispatch(self, pipeline: "_CallPipeline") -> CallResult:
        """Run a call every gate let through: the execution slot, the invoke, the relay and the group's health."""
        call = pipeline.call
        # Acquire concurrency slots (global + per-mcp_server) before invocation.
        # This is where backpressure happens: if the global or mcp_server semaphore
        # is full, this thread blocks until a slot frees up. Crucially, the call
        # starts as soon as ANY slot is freed -- it does not wait for an entire
        # batch wave to complete (unlike sequential chunking).
        #
        # The span measures only that wait (#1273). The slots are held by
        # `permit`, which outlives the span, so the invoke and its retries run
        # under the call span and the slots are released only once they return.
        cm = self.concurrency_manager
        with ExitStack() as permit:
            with pipeline.tracer.start_as_current_span("concurrency.acquire") as conc_span:
                conc_span.set_attribute("mcp.server.id", call.mcp_server)
                wait_s = permit.enter_context(cm.acquire(call.mcp_server))
                conc_span.set_attribute("concurrency.wait_ms", round(wait_s * 1000, 2))
            if wait_s > 0.01:
                logger.debug(
                    "concurrency_slot_wait",
                    call_id=call.call_id,
                    mcp_server=call.mcp_server,
                    wait_ms=round(wait_s * 1000, 2),
                )

            result = self._invoke_with_retry(
                call,
                pipeline.cancel_event,
                pipeline.effective_timeout,
                pipeline.call_start,
                pipeline.ctx,
                pipeline.target_server_id,
            )

        relayed = self._relay_upstream_task(pipeline, result)
        if relayed is not None:
            return relayed

        # Feed the group's circuit and member rotation with what the outcome says
        # about the member (failover on the call path, #275). A failure the
        # caller caused is not the member's (#1409).
        failed = result.member_outcome or MemberOutcome.UNHEALTHY
        self._report_member(pipeline, MemberOutcome.HEALTHY if result.success else failed)
        return result

    # -- gates ---------------------------------------------------------------
    #
    # Each returns None to let the call through, or a CallResult to refuse it.
    # Registered in _GATES at the bottom of this module, which is the order they
    # run in.

    def _gate_cancelled_before_execution(self, p: "_CallPipeline") -> CallResult | None:
        if p.cancel_event.is_set():
            # elapsed_ms is 0.0 rather than measured: nothing ran.
            return CallResult(
                index=p.call.index,
                call_id=p.call.call_id,
                success=False,
                error="Cancelled before execution",
                error_type="CancellationError",
                elapsed_ms=0.0,
            )
        return None

    def _gate_global_timeout(self, p: "_CallPipeline") -> CallResult | None:
        """Refuse if the batch's budget is already spent, and set what is left."""
        remaining_global = p.global_timeout - (time.perf_counter() - p.batch_start_time)
        if remaining_global <= 0:
            return CallResult(
                index=p.call.index,
                call_id=p.call.call_id,
                success=False,
                error="Global timeout exceeded",
                error_type="TimeoutError",
                elapsed_ms=0.0,
            )
        p.effective_timeout = min(p.call.timeout, remaining_global) if p.call.timeout is not None else remaining_global
        return None

    def _gate_resolve_target(self, p: "_CallPipeline") -> CallResult | None:
        """Resolve the call to a concrete backend, selecting a group member if needed.

        For a group the member is selected NOW (tenant-aware when a canary policy
        is set) so the rest of the pipeline -- cold-start, circuit breaker,
        dispatch -- targets a real backend. Policy, withdrawal and digest-pin
        checks below still key on the logical group id.

        A server named directly is dispatched to as itself and never through a
        group's selection. When it is a group member, the policy, withdrawal
        and pin gates below apply its groups' too. Before that, naming a member
        bypassed its group. Which groups own it is read by the governance
        decision, with the overlays, not here (#1488).

        Choosing a group's member is not part of that decision: a selection
        advances the group's strategy, so it is made once. The governance of a
        call naming a group, the group's scope and the selected member's,
        reads no membership.
        """
        p.mcp_server_obj = p.ctx.get_mcp_server(p.call.mcp_server)
        p.target_server_id = p.call.mcp_server
        if p.mcp_server_obj:
            return None

        p.group_obj = GROUPS.get(p.call.mcp_server)
        if p.group_obj:
            p.is_group = True
            selected_member = p.group_obj.select_member_for(p.caller_tenant_id)
            if selected_member is None:
                return p.refuse(f"No available member in group '{p.call.mcp_server}'", "NoAvailableMemberError")
            p.mcp_server_obj = selected_member
            p.target_server_id = selected_member.id.value
        elif not p.ctx.mcp_server_exists(p.call.mcp_server):
            return p.refuse(f"McpServer '{p.call.mcp_server}' not found", "McpServerNotFoundError")
        return None

    def _gate_tool_access(self, p: "_CallPipeline") -> CallResult | None:
        """Tool access policy, checked BEFORE starting the server or executing."""
        with p.tracer.start_as_current_span("policy.check_access") as policy_span:
            policy_span.set_attribute("mcp.server.id", p.call.mcp_server)
            policy_span.set_attribute("gen_ai.tool.name", p.call.tool)
            policy_span.set_attribute("policy.is_group", p.is_group)
            if p.is_group:
                p.group_obj = GROUPS.get(p.call.mcp_server)
            # Every scope `policy_scopes` names, each with the caller's tenant.
            # For a group: the group, and the policy of the member
            # `_gate_resolve_target` selected, keyed by its SERVER id (#1164).
            # For a server: its own policy, plus each group that owns it when
            # it is a member named directly. Deny wins. Decided with the
            # withdrawals and pins, as one set (#1431).
            allowed = p.governance.allowed
            policy_span.set_attribute("policy.allowed", allowed)

        if allowed:
            return None
        logger.info(
            "tool_access_denied",
            mcp_server_id=p.call.mcp_server,
            tool=p.call.tool,
            reason="tool_not_in_access_policy",
            owning_groups=list(p.owning_groups),
        )
        TOOL_ACCESS_DENIED_TOTAL.inc(mcp_server=p.call.mcp_server, tool=p.call.tool, reason="tool_not_in_access_policy")
        return p.refuse(_TOOL_ACCESS_DENIED, "ToolAccessDeniedError")

    def _gate_withdrawal(self, p: "_CallPipeline") -> CallResult | None:
        """Tool withdrawal status, checked BEFORE backend invoke (#231).

        Guarantee: per-process-after-reload (registry is config-reload-driven;
        runtime mutation is #235). Rejection is envelope-level; protocol-clean
        -32601 is #232. Semantics: projection is None -> registry unpopulated ->
        do NOT block (safe default). Only an explicit is_withdrawn_for() == True
        causes rejection.

        A group member named directly is also refused a tool withdrawn on any
        group that owns it, for every tenant or for this caller's. This mirrors
        the front door's ``_withdrawal_scopes``, which asks under the member id
        and every group that owns it, and is fail-closed. Before this, a
        withdrawal declared on a group did not hold against a call naming a
        member of that group.
        """
        if not p.governance.withdrawn:
            return None
        return self._withdrawn_refusal(p.call, p.ctx, p.caller_tenant_id, p.elapsed_ms())

    def _withdrawn_refusal(self, call: CallSpec, ctx: Any, tenant_id: str | None, elapsed_ms: float) -> CallResult:
        """The refusal of a withdrawn tool, logged and published.

        The withdrawal gate and the re-check after an approval hold give this
        same outcome.
        """
        logger.info("tool_withdrawn_rejected", mcp_server_id=call.mcp_server, tool=call.tool, tenant_id=tenant_id)
        ctx.event_bus.publish(ToolWithdrawnRejected(tenant_id=tenant_id, mcp_server=call.mcp_server, tool=call.tool))
        return CallResult(
            index=call.index,
            call_id=call.call_id,
            success=False,
            error=_withdrawn_message(call.tool),
            error_type="ToolWithdrawnError",
            elapsed_ms=elapsed_ms,
        )

    def _enforce_digest_pins(self, p: "_CallPipeline", projection: Any) -> CallResult | None:
        """Validate *projection* against every pin in ``p.pins()``. The first refusal wins.

        Usually that is a single pin, the call's own. A call naming a group
        member also carries each pin its groups declare for the tool. Each pin
        is enforced in the mode set on the id that declared it, as a call
        naming that group would enforce it, read with the pin (#1431).
        """
        for _scope, pin, enforcement in p.pins():
            refusal = self._enforce_digest_pin(p, projection, pin, enforcement)
            if refusal is not None:
                return refusal
        return None

    def _enforce_digest_pin(
        self, p: "_CallPipeline", projection: Any, pin: Any, enforcement: DigestEnforcement
    ) -> CallResult | None:
        """Validate *projection* against the tenant's *pin*; a CallResult means reject.

        *enforcement* is the ``digest_enforcement`` mode of the id that
        declared the pin, read with it.
        """
        try:
            digest_result = DigestValidator(
                DigestPolicy(
                    enforcement=enforcement,
                    unknown=DigestUnknownPolicy.BLOCK,
                    allowlist=frozenset({pin}),
                )
            ).validate_tool(projection.schema, p.call.mcp_server, p.call.call_id, tenant_id=p.caller_tenant_id)
            blocked = digest_result.blocked
            event = digest_result.event
        except Exception:  # noqa: BLE001 -- a malformed projection schema must not 500 the call path
            # Cannot compute/verify the digest: fail closed under block, else allow.
            logger.warning(
                "tool_digest_pin_unverifiable",
                mcp_server_id=p.call.mcp_server,
                tool=p.call.tool,
                tenant_id=p.caller_tenant_id,
            )
            blocked = enforcement == DigestEnforcement.BLOCK
            event = None
        if event is not None:
            p.ctx.event_bus.publish(event)
        if blocked:
            logger.info(
                "tool_digest_pin_rejected",
                mcp_server_id=p.call.mcp_server,
                tool=p.call.tool,
                tenant_id=p.caller_tenant_id,
            )
            # "for this tenant" was true while a pin could only be declared for
            # one, and became a small lie once an all-tenants pin could refuse a
            # caller who carries no tenant at all (#902).
            return p.refuse(
                f"Tool '{p.call.tool}' schema does not match its pinned digest",
                "ToolDigestMismatchError",
            )
        # Pin verified: bind the tool's approved digest to the request context so
        # that if this call is task-augmented and returns a task handle,
        # GovernedTaskStore.create_task pins the task to this digest and
        # re-verifies it fail-closed on result retrieval (#320). Each batch call
        # runs in its own contextvars.copy_context() (see execute()), so this set
        # is confined to the current call.
        set_current_tool_pin(
            CurrentToolPin(mcp_server=p.call.mcp_server, tool_name=p.call.tool, pinned_digest=pin.sha256)
        )
        return None

    def _gate_digest_pin(self, p: "_CallPipeline") -> CallResult | None:
        """Per-tenant digest pin enforcement (#233).

        If the caller's tenant pinned this tool to an approved digest, validate
        the backend's current schema against it and enforce per the server's
        configured mode. No pin -> unchanged behavior.

        NOTE: the withdrawal check above takes precedence -- a withdrawn tool is
        rejected before reaching here, so no mismatch event fires for a tool that
        is both withdrawn and pinned.

        A pinned tool whose projection is not in the registry yet cannot be
        checked here. That is the state of every backend that has not started in
        this process: the catalogue is populated by the McpServerStarted handler,
        and the cold start happens LATER in this pipeline. Left as-is, the first
        call after a gateway boot skipped the pin entirely -- one unvalidated
        call per boot per server, and gateway restarts are routine in Kubernetes
        (#601). So the check is deferred and re-run by
        _gate_deferred_digest_pin once the cold start has populated the
        catalogue.

        The pins are the governance decision's (`_decide_governance`): the one
        on the id the call named, else on the member a group selected, and
        those of the groups of a member named directly.
        """
        if not p.pins():
            return None
        if p.projection is None:
            p.digest_pin_deferred = True
            return None
        return self._enforce_digest_pins(p, p.projection)

    def _gate_circuit_breaker(self, p: "_CallPipeline") -> CallResult | None:
        """Circuit breaker / health degradation of the resolved target.

        A DEAD target is judged by why it died and by its backoff, not by its
        failure count: see `_refuse_dead_target`.

        A refusal of a group member counts as that member's failure, as a
        failed invocation does. Otherwise a member whose restart keeps failing
        stays in rotation and keeps drawing calls, and the group never fails
        over (#1361).
        """
        if not p.mcp_server_obj:
            return None
        if p.mcp_server_obj.state.value == "dead":
            return self._report_refusal(p, self._refuse_dead_target(p))
        health = getattr(p.mcp_server_obj, "health", None)
        if not (health is not None and health.should_degrade()):
            return None
        BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(mcp_server=p.target_server_id)
        refusal = p.refuse("Circuit breaker open (too many consecutive failures)", "CircuitBreakerOpen")
        return self._report_refusal(p, refusal)

    def _refuse_dead_target(self, p: "_CallPipeline") -> CallResult | None:
        """Refuse a call a DEAD target may not take now; None lets the call start it.

        A capability block is not revived by a call: only a deliberate start
        revives it. Any other dead target is judged by its backoff, not by the
        failure count that got it there. Judged by the count, every call to it
        was refused, and a call is one of the things that revive a dead server
        (#1361). Inside the backoff the call is refused and told how long to
        wait; after it, the cold-start gate starts the server.
        """
        if getattr(p.mcp_server_obj, "dead_reason_snapshot", None) in DEAD_NOT_REVIVED_BY_CALLS:
            return p.refuse(
                "A capability block is not revived by a call; start it explicitly", "CannotStartMcpServerError"
            )
        health = getattr(p.mcp_server_obj, "health", None)
        if health is None or health.can_retry():
            return None
        BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(mcp_server=p.target_server_id)
        return p.refuse(
            f"Circuit breaker open (too many consecutive failures); retry in {health.time_until_retry():.1f}s",
            "CircuitBreakerOpen",
        )

    @classmethod
    def _report_refusal(
        cls, p: "_CallPipeline", refusal: CallResult | None, cause: BaseException | None = None
    ) -> CallResult | None:
        """Report a refused group member to its group, as a failed invocation is (#1361).

        The refusal's code decides what it says about the member, or *cause*,
        the error behind it, when there is one: a start the command bus refused
        for its rate limit is Hangar's refusal, not the member failing (#1409).
        """
        if refusal is not None:
            cls._report_member(p, member_outcome(cause if cause is not None else refusal.error_type))
        return refusal

    @staticmethod
    def _report_member(p: "_CallPipeline", outcome: MemberOutcome) -> None:
        """Tell a group what *outcome* says about the member that took the call (#1409).

        Then drain it, so what the group made of the outcome -- a member out of
        rotation, the circuit opened -- reaches a subscriber on this call rather
        than on whenever somebody next edits the group (#1410).
        """
        if not (p.is_group and p.group_obj is not None):
            return
        if outcome is MemberOutcome.HEALTHY:
            p.group_obj.report_success(p.target_server_id)
        elif outcome is MemberOutcome.UNHEALTHY:
            p.group_obj.report_failure(p.target_server_id)
        else:
            # UNJUDGED: the group was told nothing, so it recorded nothing.
            return
        publish_group_events(p.ctx.event_bus, p.group_obj)

    @staticmethod
    def _refused_start(p: "_CallPipeline", e: CannotStartMcpServerError) -> CallResult:
        """A call's start the server refused, coded as `_refuse_dead_target` codes the same condition."""
        if e.reason == START_REFUSED_NOT_REVIVED_BY_CALLS:
            return p.refuse(
                "A capability block is not revived by a call; start it explicitly", "CannotStartMcpServerError"
            )
        if e.reason.startswith(START_REFUSED_IN_BACKOFF):
            BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL.inc(mcp_server=p.target_server_id)
            return p.refuse(
                f"Circuit breaker open (too many consecutive failures); retry in {e.time_until_retry:.1f}s",
                "CircuitBreakerOpen",
            )
        return p.refuse(f"Failed to start mcp_server: {e}", "McpServerStartError")

    def _gate_validators(self, p: "_CallPipeline") -> CallResult | None:
        """Interceptor validators, fail-closed BEFORE prompting for approval.

        Ordered ahead of the approval gate so a validator denial short-circuits
        without blocking on a human decision. Empty pipeline (default) allows.
        """
        denied = self._check_validators(p.call)
        if denied is None:
            return None
        denied.elapsed_ms = p.elapsed_ms()
        return denied

    def _gate_tenant_budget(self, p: "_CallPipeline") -> CallResult | None:
        """Whether the caller's tenant has a budget, and a token for this call (#1445).

        After the policy gates, so a call they refuse spends nothing. Before
        the approval hold and the cold start, so a caller with no budget, or
        over its rate, neither asks a human to approve a call that cannot run
        nor starts a stopped server. The token goes back if a later gate
        refuses the call (`_run_gates`); the slot is taken last, by
        `_enforce_tenant_budget`.

        Being before the cold start puts it before the deferred pin check
        (#601), which needs the catalogue a cold start fills. On a server that
        has not started, a caller refused here is told `TenantQuotaExceeded`,
        not a pin mismatch, and nothing starts. Pinned by
        tests/unit/test_batch_gate_precedence.py.
        """
        reserved = get_tenant_admission().reserve(p.caller_tenant_id)
        if isinstance(reserved, Refusal):
            return self._refuse_over_budget(p, reserved)
        p.reservation = reserved
        return None

    def _gate_approval(self, p: "_CallPipeline") -> CallResult | None:
        """Human approval gate, plus the re-check of everything it paused.

        The policy is configured via the server config and applied to the
        ToolAccessResolver; this uses the resolver's effective policy
        (mcp_server-specific or _global fallback).
        """
        with p.tracer.start_as_current_span("approval_gate.check") as approval_span:
            approval_span.set_attribute("mcp.server.id", p.call.mcp_server)
            approval_span.set_attribute("gen_ai.tool.name", p.call.tool)
            approval_result = self._check_approval_gate(
                p.call,
                p.resolver,
                p.ctx,
                tenant_id=p.caller_tenant_id,
                scopes=p.policy_scopes(),
                governance=p.governance,
            )
            if approval_result is not None:
                approval_span.set_attribute("approval.result", approval_result.error_type or "denied")
                approval_result.elapsed_ms = p.elapsed_ms()
                return approval_result

            # Re-establish validity after the hold. The gate blocks for up to
            # `approval_timeout_seconds` (300 by default), and every check that
            # preceded it -- effective policy, tool withdrawal, the pinned tool
            # digest -- was evaluated against the world as it was *before* that
            # pause. Config reload is a supported live operation, so withdrawing
            # a tool or tightening a policy while a decision is pending left the
            # held call to dispatch on the superseded decision.
            granted_id = getattr(_approval_loop_local, "approval_id", None)
            if granted_id is not None:
                # Any pin stands for "there is something to re-verify". The
                # callback re-verifies all of them, the group pins of a member
                # named directly included.
                pins = p.pins()
                refusal = self._revalidate_after_hold(
                    p.call,
                    p.resolver,
                    p.ctx,
                    granted_id,
                    pins[-1][1] if pins else None,
                    p.proj_registry,
                    p.caller_tenant_id,
                    lambda projection, _pin: self._enforce_digest_pins(p, projection),
                    group_id=p.call.mcp_server if p.is_group else None,
                    target_server_id=p.target_server_id,
                )
                if refusal is not None:
                    approval_span.set_attribute("approval.result", "revalidation_failed")
                    refusal.elapsed_ms = p.elapsed_ms()
                    return refusal
            if approval_span.is_recording():
                # Only while someone will read it: without a gate the label
                # costs a second L7 evaluation of the arguments.
                approval_span.set_attribute("approval.result", self._approval_pass_label(p, granted_id))
        return None

    def _approval_pass_label(self, p: "_CallPipeline", granted_id: str | None) -> str:
        """What a pass through the approval gate was, for its span (#1274).

        Read-only, and it never raises: the label reports the decision already
        made above and must not take part in it.
        """
        if granted_id is not None:
            return "approved"  # granted by a human, then revalidated
        if getattr(p.ctx, "approval_gate", None) is not None:
            return "not_required"
        try:
            # An L7 requireApproval with nobody to ask passes the gate and is
            # refused by the aggregate at dispatch (see _check_approval_gate).
            l7_rule = self._l7_approval_rule(p.call, p.ctx, policy=p.governance.l7_policy)
        except Exception:  # noqa: BLE001 -- a span label must never decide the call
            return "not_required"
        return "unavailable" if l7_rule is not None else "not_required"

    def _gate_cold_start(self, p: "_CallPipeline") -> CallResult | None:
        """Single-flight cold start of the resolved target.

        A DEAD target starts here too. A call is one of the things that revive
        a dead server (#1361), and starting it here rather than inside the
        invocation is what re-runs a deferred digest pin. It is judged again
        first: the approval gate can hold a call for minutes after the
        circuit-breaker gate let it through. A group member that is refused,
        or fails to start, counts as that member's failure. A start Hangar
        itself refused, for the command bus's rate limit, does not (#1409).
        """
        if not (p.mcp_server_obj and p.mcp_server_obj.state.value in ("cold", "dead")):
            return None
        if p.mcp_server_obj.state.value == "dead":
            refusal = self._refuse_dead_target(p)
            if refusal is not None:
                return self._report_refusal(p, refusal)
        with p.tracer.start_as_current_span("mcp_server.cold_start") as cs_span:
            cs_span.set_attribute("mcp.server.id", p.target_server_id)
            try:
                self._single_flight.do(
                    p.target_server_id,
                    lambda: p.ctx.command_bus.send(
                        StartMcpServerCommand(mcp_server_id=p.target_server_id, deliberate=False)
                    ),
                )
                cs_span.set_attribute("cold_start.result", "success")
            except CannotStartMcpServerError as e:
                # The server's own check refused the start after this gate's
                # passed: each draws the backoff's jitter afresh, or the server
                # was blocked in between. Not a failed start (#1446).
                cs_span.set_attribute("cold_start.result", "refused")
                return self._report_refusal(p, self._refused_start(p, e))
            except Exception as e:  # noqa: BLE001 -- fault-barrier: mcp_server start failure must return error result, not crash batch
                cs_span.set_attribute("cold_start.result", "error")
                record_handled_failure(cs_span, e)
                refusal = p.refuse(f"Failed to start mcp_server: {e}", "McpServerStartError")
                return self._report_refusal(p, refusal, cause=e)
        return None

    def _gate_deferred_digest_pin(self, p: "_CallPipeline") -> CallResult | None:
        """Run the pin check the cold start made possible (#601).

        The cold start published McpServerStarted, so the tool catalogue exists
        now. Still missing afterwards means the tool never appeared in the
        catalogue at all, which for a PINNED tool is unverifiable -> fail closed
        under BLOCK, matching how an uncomputable digest is treated inside the
        gate.
        """
        if not p.digest_pin_deferred:
            return None
        late_projection = p.reresolve_projection()
        if late_projection is not None:
            return self._enforce_digest_pins(p, late_projection)
        if all(enforcement != DigestEnforcement.BLOCK for _scope, _pin, enforcement in p.pins()):
            return None
        logger.info(
            "tool_digest_pin_unresolvable",
            mcp_server_id=p.call.mcp_server,
            tool=p.call.tool,
            tenant_id=p.caller_tenant_id,
        )
        return p.refuse(
            f"Tool '{p.call.tool}' is pinned for this tenant but its schema could not be verified",
            "ToolDigestMismatchError",
        )

    def _gate_cancelled_after_cold_start(self, p: "_CallPipeline") -> CallResult | None:
        if not p.cancel_event.is_set():
            return None
        return p.refuse("Cancelled after cold start", "CancellationError")

    def _relay_upstream_task(self, p: "_CallPipeline", result: CallResult) -> CallResult | None:
        """Upstream MCP task handle (ADR-014 P3).

        Two mutually exclusive outcomes, both an EARLY return BEFORE the
        group-health block (a task creation is NOT a healthy-member outcome, so
        report_success must not fire for it):

         - Relay kill-switch ON (the governed task store is wired on the app ctx,
           which happens ONLY when config.relay_tasks_enabled is True): CAPTURE
           the request context into the CallResult and return it as a success.
           This worker performs NO store write -- per ADR-014 D4 the actual
           register + TaskCreated emit runs on the MAIN LOOP at the relay seam
           (relay_seam.py), before the handle reaches the client.
         - Kill-switch OFF (store absent): byte-identical to the ADR-008
           relay-only stance -- a clean TaskRelayNotSupported rejection, so the
           client never gets an untracked, unusable handle.

        The store's mere presence on ctx is the kill-switch: the factory wires
        governed_task_store ONLY under `HAS_NATIVE_TASKS and relay_tasks_enabled`
        (see fastmcp_server/factory._enable_governed_tasks), and the real
        ApplicationContext field defaults to None. Reading it here needs no
        config plumbing into the worker.
        """
        if not (result.success and isinstance(result.result, dict) and _is_task_result(result.result)):
            return None
        if getattr(p.ctx, "governed_task_store", None) is not None:
            logger.debug(
                "upstream_task_result_captured_for_relay",
                mcp_server=p.call.mcp_server,
                tool=p.call.tool,
                call_id=p.call.call_id,
            )
            return CallResult(
                index=p.call.index,
                call_id=p.call.call_id,
                success=True,
                result=result.result,
                elapsed_ms=result.elapsed_ms,
                relay_capture=RelayCapture(
                    identity=get_identity_context(),
                    pin=get_current_tool_pin(),
                    target_server_id=p.target_server_id,
                    correlation_id=p.call.call_id,
                    upstream=result.result,
                    logical_mcp_server=p.call.mcp_server,
                    tool=p.call.tool,
                ),
            )
        logger.warning(
            "upstream_task_result_rejected",
            mcp_server=p.call.mcp_server,
            tool=p.call.tool,
            call_id=p.call.call_id,
        )
        return CallResult(
            index=p.call.index,
            call_id=p.call.call_id,
            success=False,
            error=(
                "Upstream returned an MCP task handle; Hangar does not yet relay "
                "or govern task results (relay-only, ADR-008). The task is not "
                "tracked, so the handle is unusable."
            ),
            error_type="TaskRelayNotSupported",
            elapsed_ms=result.elapsed_ms,
        )

    def _invoke_with_retry(
        self,
        call: CallSpec,
        cancel_event: threading.Event,
        effective_timeout: float,
        call_start: float,
        ctx: Any,
        target_server_id: str | None = None,
    ) -> CallResult:
        """Perform the tool invocation, optionally with retries.

        This method runs while concurrency slots are held. It contains the
        actual I/O (command bus send) and retry logic extracted from
        _execute_call for clarity.

        Args:
            call: Call specification.
            cancel_event: Event to check for cancellation.
            effective_timeout: Timeout for this call.
            call_start: Monotonic time when the call started.
            ctx: Application context.

        Returns:
            CallResult for this call.
        """

        # Define the invocation operation for retry
        tracer = get_tracer(__name__)

        # Dispatch to the resolved target: the selected group member when
        # call.mcp_server is a group, otherwise the server itself.
        dispatch_server_id = target_server_id or call.mcp_server

        # Interceptor mutators (request): transform the outgoing arguments payload
        # once, before dispatch (and before any retry). Empty pipeline (default)
        # returns the arguments unchanged, preserving current behavior.
        mutated_arguments = self._mutate("tools/call", "request", call.arguments or {}, call.call_id)

        def do_invoke() -> dict[str, Any]:
            with tracer.start_as_current_span("command.send.InvokeToolCommand") as cmd_span:
                cmd_span.set_attribute("mcp.server.id", dispatch_server_id)
                cmd_span.set_attribute("gen_ai.tool.name", call.tool)
                cmd_span.set_attribute("command.timeout", effective_timeout)
                command = InvokeToolCommand(
                    mcp_server_id=dispatch_server_id,
                    tool_name=call.tool,
                    arguments=mutated_arguments,
                    timeout=effective_timeout,
                    # A granted (and revalidated) approval converts the L7
                    # requireApproval verdict in the aggregate (#921); None
                    # when nothing was granted, and deny still wins inside.
                    l7_approval_id=getattr(_approval_loop_local, "approval_id", None),
                    progress_token=call.progress_token,
                )
                result = ctx.command_bus.send(command)
                cmd_span.set_attribute("command.result", "success")
                return cast(dict[str, Any], result)

        # Execute with retry, under whichever policy applies (#1162).
        retry_result: RetryResult | None = None
        policy = _retry_policy_for(call)
        if policy is not None:
            with tracer.start_as_current_span("invoke_with_retry") as retry_span:
                retry_span.set_attribute("retry.max_attempts", policy.max_attempts)
                retry_span.set_attribute("retry.backoff", str(policy.backoff))
                retry_span.set_attribute("mcp.server.id", call.mcp_server)
                retry_span.set_attribute("gen_ai.tool.name", call.tool)
                retry_result = retry_sync(
                    operation=do_invoke,
                    policy=policy,
                    mcp_server=call.mcp_server,
                    operation_name=call.tool,
                )
                retry_span.set_attribute("retry.attempts", retry_result.attempt_count)
                retry_span.set_attribute("retry.success", retry_result.success)
            if retry_result.success:
                result = retry_result.result
            else:
                # All retries exhausted
                elapsed_ms = (time.perf_counter() - call_start) * 1000
                error_type = type(retry_result.final_error).__name__ if retry_result.final_error else "UnknownError"
                error_msg = str(retry_result.final_error) if retry_result.final_error else "Unknown error"

                _log_call_failure(call, retry_result.final_error, error_type, elapsed_ms)

                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=error_msg,
                    error_type=error_type,
                    elapsed_ms=elapsed_ms,
                    member_outcome=member_outcome(retry_result.final_error),
                    retry_metadata=RetryMetadata(
                        attempts=retry_result.attempt_count,
                        retries=[a.error_type for a in retry_result.attempts],
                        total_time_ms=retry_result.total_time_s * 1000,
                    ),
                )
        else:
            # No retry - direct execution
            try:
                result = do_invoke()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: tool invocation failure must return error result, not crash batch
                elapsed_ms = (time.perf_counter() - call_start) * 1000
                error_type = type(e).__name__

                _log_call_failure(call, e, error_type, elapsed_ms)

                return CallResult(
                    index=call.index,
                    call_id=call.call_id,
                    success=False,
                    error=str(e),
                    error_type=error_type,
                    elapsed_ms=elapsed_ms,
                    member_outcome=member_outcome(e),
                )

        # Interceptor mutators (response): transform the returned result payload
        # after a successful invoke, before the size check and building the
        # success CallResult. Empty pipeline (default) returns it unchanged.
        result = self._mutate("tools/call", "response", cast(dict[str, Any], result), call.call_id)

        elapsed_ms = (time.perf_counter() - call_start) * 1000

        # Check response size and truncate if needed
        truncated = False
        truncated_reason = None
        original_size = None

        result_json = json.dumps(result)
        result_size = len(result_json.encode("utf-8"))

        # A call whose caller takes the whole result is not cut (#1453).
        if result_size > MAX_RESPONSE_SIZE_BYTES and not call.whole_result:
            truncated = True
            truncated_reason = "response_size_exceeded"
            original_size = result_size
            result = None
            BATCH_TRUNCATIONS_TOTAL.inc(reason="per_call")
            logger.warning(
                "batch_call_truncated",
                call_id=call.call_id,
                mcp_server=call.mcp_server,
                tool=call.tool,
                size_bytes=result_size,
                limit_bytes=MAX_RESPONSE_SIZE_BYTES,
            )

        logger.debug(
            "batch_call_completed",
            call_id=call.call_id,
            mcp_server=call.mcp_server,
            tool=call.tool,
            success=True,
            elapsed_ms=round(elapsed_ms, 2),
            retry_attempts=retry_result.attempt_count if retry_result else 1,
        )

        # Build retry metadata if retries were used
        retry_meta = None
        if retry_result:
            retry_meta = RetryMetadata(
                attempts=retry_result.attempt_count,
                retries=[a.error_type for a in retry_result.attempts],
                total_time_ms=retry_result.total_time_s * 1000,
            )

        return CallResult(
            index=call.index,
            call_id=call.call_id,
            success=True,
            result=result,
            elapsed_ms=elapsed_ms,
            truncated=truncated,
            truncated_reason=truncated_reason,
            original_size_bytes=original_size,
            retry_metadata=retry_meta,
        )


def format_result_dict(result: CallResult) -> dict[str, Any]:
    """Format a CallResult into a response dictionary.

    Args:
        result: The call result to format.

    Returns:
        Dictionary suitable for JSON serialization.
    """
    d: dict[str, Any] = {
        "index": result.index,
        "call_id": result.call_id,
        "success": result.success,
        "result": result.result,
        "error": result.error,
        "error_type": result.error_type,
        "elapsed_ms": round(result.elapsed_ms, 2),
    }

    if result.truncated:
        d["truncated"] = True
        d["truncated_reason"] = result.truncated_reason
        d["original_size_bytes"] = result.original_size_bytes

    if result.continuation_id:
        d["continuation_id"] = result.continuation_id

    if result.retry_metadata:
        d["retry_metadata"] = result.retry_metadata.to_dict()

    return d


#: The order the gates run in. This IS the precedence contract -- which gate
#: answers decides what the caller does next, so reordering two lines here
#: changes behaviour. tests/unit/test_batch_gate_precedence.py pins it by
#: arranging pairs to fail at once and asserting which one wins.
_GATES = (
    BatchExecutor._gate_cancelled_before_execution,
    BatchExecutor._gate_global_timeout,
    BatchExecutor._gate_resolve_target,
    BatchExecutor._gate_tool_access,
    BatchExecutor._gate_withdrawal,
    BatchExecutor._gate_digest_pin,
    BatchExecutor._gate_circuit_breaker,
    BatchExecutor._gate_validators,
    BatchExecutor._gate_tenant_budget,
    BatchExecutor._gate_approval,
    BatchExecutor._gate_cold_start,
    BatchExecutor._gate_deferred_digest_pin,
    BatchExecutor._gate_cancelled_after_cold_start,
)
