"""The merge behind ``decide()``: the effective policy for one loose-string scope (#1784).

Everything here asks in ``(mcp_server_id, group_id, member_id, member_server_id)``,
the signature every scope-key defect went through (#1763): ``member_id`` is the
caller's tenant, and nothing but discipline stops a server id landing in it.
So this is a private module, and these are functions over a
:class:`ToolAccessResolver`, not methods on it: a method is reachable from
anything holding the resolver, while an import is something a contract can
forbid. ``.importlinter`` lets ``domain/services/access_decision.py`` import this
module and nothing else, and ``decide()`` is the way in from anywhere else.

The resolver keeps the policy tables, the cache and their invalidation; this
module reads them under its lock. The two are one unit split at that seam,
which is why the private names cross it. It logs on the resolver's logger, so
the ``front_door_denied_no_tenant`` line reads as it always did.
"""

from ...logging_config import should_log_now
from ..value_objects import ToolAccessPolicy
from ..value_objects.access import DEFAULT_KIND, PolicyKind
from .tool_access_resolver import ToolAccessResolver, logger

# Sentinel policy that denies every tool.  deny_list=("*",) matches any
# tool name via fnmatch, so is_tool_allowed() returns False for all names.
_DENY_ALL_POLICY: ToolAccessPolicy = ToolAccessPolicy(deny_list=("*",))


def resolve_effective_policy(
    resolver: ToolAccessResolver,
    mcp_server_id: str,
    group_id: str | None = None,
    member_id: str | None = None,
    *,
    member_server_id: str | None = None,
    kind: PolicyKind = DEFAULT_KIND,
) -> ToolAccessPolicy:
    """Get the effective access policy for a specific context.

    For standalone mcp_servers: returns mcp_server-level policy.
    For group members: merges mcp_server -> group -> member policies.

    Args:
        mcp_server_id: McpServer identifier.
        group_id: Optional group identifier (for group member context).
        member_id: The CALLER's tenant. On the standalone branch it selects
            the per-tenant policy; in front_door mode its absence is the
            fail-closed missing-identity branch.
        member_server_id: The group member the call was routed to, when the
            caller knows it. Group member policies are registered under the
            member SERVER id (`groups.<g>.members.<m>.tools`, and the REST
            `member/<group>:<member>` scope), so without this the group
            branch looked them up under the tenant and never found them --
            a documented deny list that failed open (#1164). Defaults to
            *member_id* so a caller using the resolver's own vocabulary
            (member id in `member_id`) resolves exactly as before.
        kind: What is being governed -- tools, prompts or resources. Each
            kind resolves against the policies registered for that kind
            alone, so a tool deny never silently governs a prompt.

    Returns:
        The effective ToolAccessPolicy for this context.
    """
    # Build cache key. The group scope names both halves: two tenants
    # routed to the same member can resolve differently (the member's own
    # per-tenant policy), and so can one tenant routed to two members.
    if group_id and (member_id or member_server_id):
        member_scope = member_server_id or member_id
        scope_key = f"group:{group_id}:member:{member_scope}"
        if member_id:
            scope_key = f"{scope_key}:tenant:{member_id}"
    elif member_id and not group_id:
        scope_key = f"mcp_server:{mcp_server_id}:member:{member_id}"
    else:
        scope_key = f"mcp_server:{mcp_server_id}"
    cache_key = (kind, scope_key)

    # Check cache first
    with resolver._lock:
        if cache_key in resolver._policy_cache:
            return resolver._policy_cache[cache_key]

        # Compute effective policy
        effective = _compute_effective_policy(resolver, mcp_server_id, group_id, member_id, kind, member_server_id)

        # Cache it
        resolver._policy_cache[cache_key] = effective
        return effective


def _compute_effective_policy(
    resolver: ToolAccessResolver,
    mcp_server_id: str,
    group_id: str | None,
    member_id: str | None,
    kind: PolicyKind = DEFAULT_KIND,
    member_server_id: str | None = None,
) -> ToolAccessPolicy:
    """Compute effective policy by merging all applicable levels.

    Must be called with lock held.

    Resolution order (highest priority last, i.e. narrower wins):
      _global -> mcp_server -> group -> member server -> tenant

    The _global policy (keyed as "_global") is set by the agent when a
    cloud policy uses mcp_server_id="*".  It acts as a floor: if no
    mcp_server-specific policy exists the global policy is used instead; if
    a mcp_server-specific policy exists the two are merged so the narrower
    of the two wins (deny union, allow intersection).
    """
    explicit_mcp_server_policy = resolver._mcp_server_policies.get((mcp_server_id, kind))
    global_policy = resolver._mcp_server_policies.get(("_global", kind), ToolAccessPolicy())

    if explicit_mcp_server_policy is None:
        mcp_server_policy = global_policy
    else:
        mcp_server_policy = ToolAccessPolicy.merge(global_policy, explicit_mcp_server_policy)

    # Fail-closed default: in front_door mode a caller with NO tenant
    # identity (member_id is None) is DENIED regardless of target. This
    # fires before the standalone/group branches below so an unauthenticated
    # external caller can never reach a tool via the group path either.
    if member_id is None and resolver._topology_mode == "front_door":
        # Say so. Fail-closed is right; fail-closed and SILENT is what hides
        # a wiring bug behind a policy-shaped symptom -- it is what made #856
        # cost hours instead of minutes, because every observable surface was
        # healthy and the one thing that disagreed produced no signal (#862).
        #
        # Throttled to once a minute: this branch fires on every request
        # while the condition lasts, and a front door denying everything is a
        # standing state, so the first line is the signal and the next
        # thousand would bury it. Keyed by server so a single misconfigured
        # upstream does not silence the rest.
        if should_log_now(f"front_door_denied_no_tenant:{mcp_server_id}"):
            logger.warning(
                "front_door_denied_no_tenant mcp_server_id=%s -- the caller carried no tenant identity, "
                "so every tool is denied. This is the missing-identity branch, NOT a policy decision: "
                "check that authentication is configured and that the identity reaches the handler.",
                mcp_server_id,
            )
        return _DENY_ALL_POLICY

    # If member_id present but no group_id: server→member merge (standalone tenant policy)
    if member_id and not group_id:
        standalone_member_policy = resolver._standalone_member_policies.get(
            (mcp_server_id, member_id, kind), ToolAccessPolicy()
        )
        return ToolAccessPolicy.merge(mcp_server_policy, standalone_member_policy)

    # If no group context at all (egress mode here — front_door already
    # returned above; member_id is None), fall back to server-level policy.
    if not group_id:
        return mcp_server_policy

    # Get group policy
    group_policy = resolver._group_policies.get((group_id, kind), ToolAccessPolicy())

    # Get member policy. Keyed by the member SERVER id, which is what
    # `groups.<g>.members.<m>.tools` and the REST `member/<g>:<m>` scope
    # register -- never the tenant (#1164). `member_server_id` is what the
    # dispatching caller resolved; `member_id` is the fallback for a caller
    # speaking the resolver's own vocabulary (the tests, and any caller for
    # which tenant and member id coincide).
    member_scope_id = member_server_id or member_id
    member_policy = ToolAccessPolicy()
    mapped_mcp_server_id: str | None = None
    if member_scope_id is not None:
        member_policy = resolver._member_policies.get((group_id, member_scope_id, kind), ToolAccessPolicy())
        mapped_mcp_server_id = resolver._member_mcp_server_mapping.get((group_id, member_scope_id))
    if mapped_mcp_server_id and mapped_mcp_server_id != mcp_server_id:
        mapped_mcp_server_policy = resolver._mcp_server_policies.get((mapped_mcp_server_id, kind), ToolAccessPolicy())
        # Merge mapped mcp_server policy with base mcp_server policy
        mcp_server_policy = ToolAccessPolicy.merge(mcp_server_policy, mapped_mcp_server_policy)

    # The caller's own per-tenant policy, narrower than everything above it.
    # Both spellings are consulted, the same way #1036 consults both for the
    # group scope: `tool_access.member.<tenant>` can be declared on the group
    # and on the member server, and a call routed through a group must honour
    # whichever the operator wrote.
    tenant_policy = ToolAccessPolicy()
    if member_id is not None:
        for scope in (mcp_server_id, mapped_mcp_server_id or member_scope_id):
            if scope is None:
                continue
            tenant_policy = ToolAccessPolicy.merge(
                tenant_policy,
                resolver._standalone_member_policies.get((scope, member_id, kind), ToolAccessPolicy()),
            )

    # Four-level merge: mcp_server -> group -> member server -> tenant
    step1 = ToolAccessPolicy.merge(mcp_server_policy, group_policy)
    step2 = ToolAccessPolicy.merge(step1, member_policy)

    return ToolAccessPolicy.merge(step2, tenant_policy)


def is_allowed(
    resolver: ToolAccessResolver,
    mcp_server_id: str,
    name: str,
    *,
    kind: PolicyKind = DEFAULT_KIND,
    group_id: str | None = None,
    member_id: str | None = None,
    member_server_id: str | None = None,
) -> bool:
    """Quick check if a named tool / prompt / resource is allowed in context.

    The one decision every projected surface asks (#1028). A prompt name and
    a resource URI are matched by the same fnmatch patterns a tool name is:
    the pattern language does not change with the kind, only which policies
    are consulted does.

    Args:
        mcp_server_id: McpServer identifier.
        name: Tool name, prompt name, or -- for resources -- the UPSTREAM
            URI, not the ``hangar://`` projection of it.
        kind: What *name* is.
        group_id: Optional group identifier.
        member_id: The caller's tenant (see :func:`resolve_effective_policy`).
        member_server_id: The group member this call routes to, when known.

    Returns:
        True if allowed (including approval-gated), False otherwise.
    """
    policy = resolve_effective_policy(
        resolver, mcp_server_id, group_id, member_id, member_server_id=member_server_id, kind=kind
    )
    return policy.is_tool_allowed(name)
