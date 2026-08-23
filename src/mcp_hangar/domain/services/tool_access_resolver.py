"""Tool access resolver domain service.

Resolves the effective access policy for any mcp_server/group/member combination.
Handles the three-level merge: mcp_server -> group -> member.
Caches effective policies per-member for performance.

Keyed by *kind* since #1028: a policy is registered for ``(scope target, kind)``
where kind is ``tool``, ``prompt`` or ``resource``. Prompts and resources are
worth governing too, and giving them their own resolver would have grown a
second, weaker copy of the merge semantics, the front-door fail-closed branch
and the withdrawal overlays. One resolver, three kinds -- so listing and
fetching cannot drift apart on any of them.

``kind`` defaults to ``"tool"`` on every entry point, so a config written before
#1028 registers and resolves exactly the policies it always did. Kinds are
independent: a policy defined for prompts on one server says nothing about
tools on that server, and nothing about prompts on another -- the same
"undefined scope is unrestricted, defined scope is enforced" rule tools have
always had, applied per kind rather than reinvented for the new ones.
"""

import logging
import threading
from typing import TYPE_CHECKING, Any, Literal

from ...logging_config import should_log_now
from ..model.tool_catalog import ToolSchema
from ..value_objects import ToolAccessPolicy

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Topology modes for unauthenticated-caller resolution.
#   "egress"     – server is a back-end proxy used by trusted callers; no
#                  caller identity → fall back to the server-level policy.
#                  This is the default (backward-compatible).
#   "front_door" – server faces untrusted/external callers; no caller
#                  identity → DENY.  Absence of mode defaults to "egress"
#                  (not "front_door") so existing deployments are not
#                  broken, but a deployment that explicitly sets
#                  "front_door" is never silently promoted to the wider
#                  egress behaviour.
TopologyMode = Literal["egress", "front_door"]
_DEFAULT_MODE: TopologyMode = "egress"

# What a policy governs. A policy is keyed `(scope target, kind)`; the default
# everywhere is "tool", which is what every pre-#1028 registration meant.
PolicyKind = Literal["tool", "prompt", "resource"]
DEFAULT_KIND: PolicyKind = "tool"

# Sentinel policy that denies every tool.  deny_list=("*",) matches any
# tool name via fnmatch, so is_tool_allowed() returns False for all names.
_DENY_ALL_POLICY: ToolAccessPolicy = ToolAccessPolicy(deny_list=("*",))


def _scope_label(scope: str, kind: str) -> str:
    """Describe a registered scope, naming the kind only when it is not tool."""
    return scope if kind == DEFAULT_KIND else f"{scope}[{kind}]"


class ToolAccessResolver:
    """Resolves effective tool access policy for any mcp_server/group/member combination.

    Handles the three-level merge: mcp_server -> group -> member.
    Caches effective policies per-member for performance.
    Invalidates cache on config reload.

    Thread-safe: uses RLock for cache access.
    """

    def __init__(self) -> None:
        """Initialize the resolver with empty caches."""
        self._lock = threading.RLock()
        # Cache key format: (kind, scope key), where scope key is
        # - "mcp_server:{mcp_server_id}" for standalone mcp_servers
        # - "group:{group_id}:member:{member_id}" for group members
        # - "mcp_server:{mcp_server_id}:member:{member_id}" for standalone server→member
        self._policy_cache: dict[tuple[str, str], ToolAccessPolicy] = {}

        # Policy sources - set by external config loader. Every key carries the
        # kind it governs, so one kind's policy never resolves for another.
        # Maps (mcp_server_id, kind) -> ToolAccessPolicy
        self._mcp_server_policies: dict[tuple[str, str], ToolAccessPolicy] = {}
        # Maps (group_id, kind) -> ToolAccessPolicy
        self._group_policies: dict[tuple[str, str], ToolAccessPolicy] = {}
        # Maps (group_id, member_id, kind) -> ToolAccessPolicy
        self._member_policies: dict[tuple[str, str, str], ToolAccessPolicy] = {}
        # Maps (group_id, member_id) -> mcp_server_id (for resolving member's mcp_server).
        # Kind-independent: which server a member is, is not a policy.
        self._member_mcp_server_mapping: dict[tuple[str, str], str] = {}
        # Maps (mcp_server_id, tenant_id, kind) -> ToolAccessPolicy for standalone server→member merge
        self._standalone_member_policies: dict[tuple[str, str, str], ToolAccessPolicy] = {}

        # Topology mode controls what happens when caller has no identity
        # (member_id is None and no group context).
        # See module-level TopologyMode for semantics.
        self._topology_mode: TopologyMode = _DEFAULT_MODE

    def set_mcp_server_policy(
        self, mcp_server_id: str, policy: ToolAccessPolicy, *, kind: PolicyKind = DEFAULT_KIND
    ) -> None:
        """Set the access policy for a mcp_server.

        Args:
            mcp_server_id: McpServer identifier.
            policy: Access policy to apply.
            kind: What the policy governs -- tools (the default and the only
                pre-#1028 meaning), prompts, or resources.
        """
        with self._lock:
            if policy.is_unrestricted():
                self._mcp_server_policies.pop((mcp_server_id, kind), None)
            else:
                self._mcp_server_policies[(mcp_server_id, kind)] = policy
            # Invalidate cache for this mcp_server
            self._invalidate_mcp_server_cache(mcp_server_id)

    def set_provider_policy(self, provider_id: str, policy: ToolAccessPolicy) -> None:
        """Legacy alias for set_mcp_server_policy."""
        self.set_mcp_server_policy(provider_id, policy)

    def get_configured_policy(self, scope: str, target_id: str) -> ToolAccessPolicy | None:
        """Return the policy currently configured for a scope/target, if any.

        Read access exists so callers performing a partial update can preserve
        the fields they are not restating -- notably ``approval_list``, which a
        REST update that carries only allow/deny lists would otherwise drop,
        silently removing the human-consent gate from a tool.

        Args:
            scope: One of "provider"/"mcp_server", "group", or "member".
                Member targets are addressed as "group_id:member_id".
            target_id: Identifier within the scope.

        Returns:
            The configured policy, or None when the target has none.
        """
        with self._lock:
            if scope in ("provider", "mcp_server"):
                return self._mcp_server_policies.get((target_id, DEFAULT_KIND))
            if scope == "group":
                return self._group_policies.get((target_id, DEFAULT_KIND))
            if scope == "member":
                group_id, _, member_id = target_id.partition(":")
                return self._member_policies.get((group_id, member_id or target_id, DEFAULT_KIND))
            return None

    def set_group_policy(self, group_id: str, policy: ToolAccessPolicy, *, kind: PolicyKind = DEFAULT_KIND) -> None:
        """Set the access policy for a group.

        Args:
            group_id: Group identifier.
            policy: Access policy to apply to all members.
            kind: What the policy governs (see :meth:`set_mcp_server_policy`).
        """
        with self._lock:
            if policy.is_unrestricted():
                self._group_policies.pop((group_id, kind), None)
            else:
                self._group_policies[(group_id, kind)] = policy
            # Invalidate cache for all members in this group
            self._invalidate_group_cache(group_id)

    def set_member_policy(
        self,
        group_id: str,
        member_id: str,
        policy: ToolAccessPolicy,
        mcp_server_id: str | None = None,
        provider_id: str | None = None,
        kind: PolicyKind = DEFAULT_KIND,
    ) -> None:
        """Set the access policy for a specific group member.

        Args:
            group_id: Group identifier.
            member_id: Member identifier within the group.
            policy: Access policy for this member.
            mcp_server_id: The mcp_server_id this member maps to (for policy inheritance).
            kind: What the policy governs (see :meth:`set_mcp_server_policy`).
        """
        resolved_mcp_server_id = mcp_server_id or provider_id
        key = (group_id, member_id, kind)
        with self._lock:
            if policy.is_unrestricted():
                self._member_policies.pop(key, None)
            else:
                self._member_policies[key] = policy

            if resolved_mcp_server_id:
                self._member_mcp_server_mapping[(group_id, member_id)] = resolved_mcp_server_id

            # Invalidate cache for this member
            self._policy_cache.pop((kind, f"group:{group_id}:member:{member_id}"), None)

    def set_standalone_member_policy(
        self,
        mcp_server_id: str,
        member_id: str,
        policy: ToolAccessPolicy,
        *,
        kind: PolicyKind = DEFAULT_KIND,
    ) -> None:
        """Set a per-tenant policy for a standalone mcp_server (server→member merge).

        Args:
            mcp_server_id: McpServer identifier.
            member_id: Tenant/member identifier (e.g. ``tenant:a``).
            policy: Access policy for this member.
            kind: What the policy governs (see :meth:`set_mcp_server_policy`).
        """
        key = (mcp_server_id, member_id, kind)
        with self._lock:
            if policy.is_unrestricted():
                self._standalone_member_policies.pop(key, None)
            else:
                self._standalone_member_policies[key] = policy
            # Invalidate cache for this (server, member) pair
            self._policy_cache.pop((kind, f"mcp_server:{mcp_server_id}:member:{member_id}"), None)

    def iter_registered_policies(self, *, kind: PolicyKind | None = None) -> list[tuple[str, ToolAccessPolicy]]:
        """Return every registered policy as ``(scope_description, policy)``.

        Used by the startup reachability check to answer "does this
        configuration actually ask for the approval gate?" without reaching into
        the resolver's private dicts. Reads a snapshot under the lock.

        The description of a tool policy is unchanged; a policy of another kind
        carries a ``[kind]`` suffix so the two are distinguishable in a log line.

        Args:
            kind: Return only policies of this kind. A caller that asks a
                kind-specific question must pass it: since #1028 this list
                carries prompt and resource policies too, and the reachability
                check read them as if they were tools -- demanding an approval
                gate for a policy no path can gate (#1043). ``None`` keeps the
                every-kind view for callers that want an inventory.
        """
        with self._lock:
            registered: list[tuple[str, PolicyKind, ToolAccessPolicy]] = []
            for (mcp_server_id, policy_kind), policy in self._mcp_server_policies.items():
                registered.append((f"mcp_server:{mcp_server_id}", policy_kind, policy))
            for (group_id, policy_kind), policy in self._group_policies.items():
                registered.append((f"group:{group_id}", policy_kind, policy))
            for (group_id, member_id, policy_kind), policy in self._member_policies.items():
                registered.append((f"group:{group_id}:member:{member_id}", policy_kind, policy))
            for (mcp_server_id, member_id, policy_kind), policy in self._standalone_member_policies.items():
                registered.append((f"mcp_server:{mcp_server_id}:member:{member_id}", policy_kind, policy))
        # One filter over the collected list rather than a condition inside each
        # loop: same answer, four fewer decision paths to keep covered.
        return [
            (_scope_label(scope, policy_kind), policy)
            for scope, policy_kind, policy in registered
            if kind is None or policy_kind == kind
        ]

    @property
    def topology_mode(self) -> TopologyMode:
        """Return the current topology mode."""
        with self._lock:
            return self._topology_mode

    def set_topology_mode(self, mode: TopologyMode) -> None:
        """Set the topology mode that controls unauthenticated-caller resolution.

        Args:
            mode: "egress" (default) or "front_door".
                  "egress"     – member_id=None → server-level policy (backward compat).
                  "front_door" – member_id=None → DENY (fail-closed for external callers).
        """
        with self._lock:
            self._topology_mode = mode
            # Invalidate the cache keyed without a member_id so the new
            # mode is reflected immediately on the next resolve call.
            keys_to_remove = [k for k in self._policy_cache if ":member:" not in k[1]]
            for key in keys_to_remove:
                self._policy_cache.pop(key, None)

    def remove_mcp_server_policy(self, mcp_server_id: str) -> None:
        """Remove EVERY access policy for a mcp_server -- tool, prompt and resource.

        The one caller is the hot-unload path, and unloading a server retires
        the whole server, not one kind of policy about it. Removing a single
        kind would leave the other two behind for an id that is free to be
        loaded again, so a later server inheriting that id would be governed by
        its predecessor's rules (#1028).

        Args:
            mcp_server_id: McpServer identifier.
        """
        with self._lock:
            for key in [k for k in self._mcp_server_policies if k[0] == mcp_server_id]:
                self._mcp_server_policies.pop(key, None)
            self._invalidate_mcp_server_cache(mcp_server_id)

    def remove_provider_policy(self, provider_id: str) -> None:
        """Legacy alias for remove_mcp_server_policy."""
        self.remove_mcp_server_policy(provider_id)

    def remove_group_policy(self, group_id: str) -> None:
        """Remove the tool access policy for a group.

        Args:
            group_id: Group identifier.
        """
        with self._lock:
            self._group_policies.pop((group_id, DEFAULT_KIND), None)
            self._invalidate_group_cache(group_id)

    def remove_member_policy(self, group_id: str, member_id: str) -> None:
        """Remove the tool access policy for a group member.

        Args:
            group_id: Group identifier.
            member_id: Member identifier.
        """
        with self._lock:
            self._member_policies.pop((group_id, member_id, DEFAULT_KIND), None)
            self._member_mcp_server_mapping.pop((group_id, member_id), None)
            self._policy_cache.pop((DEFAULT_KIND, f"group:{group_id}:member:{member_id}"), None)

    def resolve_effective_policy(
        self,
        mcp_server_id: str,
        group_id: str | None = None,
        member_id: str | None = None,
        *,
        kind: PolicyKind = DEFAULT_KIND,
    ) -> ToolAccessPolicy:
        """Get the effective access policy for a specific context.

        For standalone mcp_servers: returns mcp_server-level policy.
        For group members: merges mcp_server -> group -> member policies.

        Args:
            mcp_server_id: McpServer identifier.
            group_id: Optional group identifier (for group member context).
            member_id: Optional member identifier (for group member context).
            kind: What is being governed -- tools, prompts or resources. Each
                kind resolves against the policies registered for that kind
                alone, so a tool deny never silently governs a prompt.

        Returns:
            The effective ToolAccessPolicy for this context.
        """
        # Build cache key
        if group_id and member_id:
            scope_key = f"group:{group_id}:member:{member_id}"
        elif member_id and not group_id:
            scope_key = f"mcp_server:{mcp_server_id}:member:{member_id}"
        else:
            scope_key = f"mcp_server:{mcp_server_id}"
        cache_key = (kind, scope_key)

        # Check cache first
        with self._lock:
            if cache_key in self._policy_cache:
                return self._policy_cache[cache_key]

            # Compute effective policy
            effective = self._compute_effective_policy(mcp_server_id, group_id, member_id, kind)

            # Cache it
            self._policy_cache[cache_key] = effective
            return effective

    def _compute_effective_policy(
        self,
        mcp_server_id: str,
        group_id: str | None,
        member_id: str | None,
        kind: PolicyKind = DEFAULT_KIND,
    ) -> ToolAccessPolicy:
        """Compute effective policy by merging all applicable levels.

        Must be called with lock held.

        Resolution order (highest priority last, i.e. narrower wins):
          _global -> mcp_server -> group -> member

        The _global policy (keyed as "_global") is set by the agent when a
        cloud policy uses mcp_server_id="*".  It acts as a floor: if no
        mcp_server-specific policy exists the global policy is used instead; if
        a mcp_server-specific policy exists the two are merged so the narrower
        of the two wins (deny union, allow intersection).
        """
        explicit_mcp_server_policy = self._mcp_server_policies.get((mcp_server_id, kind))
        global_policy = self._mcp_server_policies.get(("_global", kind), ToolAccessPolicy())

        if explicit_mcp_server_policy is None:
            mcp_server_policy = global_policy
        else:
            mcp_server_policy = ToolAccessPolicy.merge(global_policy, explicit_mcp_server_policy)

        # Fail-closed default: in front_door mode a caller with NO tenant
        # identity (member_id is None) is DENIED regardless of target. This
        # fires before the standalone/group branches below so an unauthenticated
        # external caller can never reach a tool via the group path either.
        if member_id is None and self._topology_mode == "front_door":
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
            standalone_member_policy = self._standalone_member_policies.get(
                (mcp_server_id, member_id, kind), ToolAccessPolicy()
            )
            return ToolAccessPolicy.merge(mcp_server_policy, standalone_member_policy)

        # If no group context at all (egress mode here — front_door already
        # returned above; member_id is None), fall back to server-level policy.
        if not group_id:
            return mcp_server_policy

        # Get group policy
        group_policy = self._group_policies.get((group_id, kind), ToolAccessPolicy())

        # Get member policy (only when a concrete member_id is present;
        # a group context without a member resolves to server+group only).
        member_policy = ToolAccessPolicy()
        mapped_mcp_server_id: str | None = None
        if member_id is not None:
            member_policy = self._member_policies.get((group_id, member_id, kind), ToolAccessPolicy())
            mapped_mcp_server_id = self._member_mcp_server_mapping.get((group_id, member_id))
        if mapped_mcp_server_id and mapped_mcp_server_id != mcp_server_id:
            mapped_mcp_server_policy = self._mcp_server_policies.get((mapped_mcp_server_id, kind), ToolAccessPolicy())
            # Merge mapped mcp_server policy with base mcp_server policy
            mcp_server_policy = ToolAccessPolicy.merge(mcp_server_policy, mapped_mcp_server_policy)

        # Three-level merge: mcp_server -> group -> member
        step1 = ToolAccessPolicy.merge(mcp_server_policy, group_policy)
        step2 = ToolAccessPolicy.merge(step1, member_policy)

        return step2

    def is_allowed(
        self,
        mcp_server_id: str,
        name: str,
        *,
        kind: PolicyKind = DEFAULT_KIND,
        group_id: str | None = None,
        member_id: str | None = None,
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
            member_id: Optional member identifier.

        Returns:
            True if allowed (including approval-gated), False otherwise.
        """
        policy = self.resolve_effective_policy(mcp_server_id, group_id, member_id, kind=kind)
        return policy.is_tool_allowed(name)

    def is_tool_allowed(
        self,
        mcp_server_id: str,
        tool_name: str,
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> bool:
        """Quick check if a specific tool is allowed in context.

        Args:
            mcp_server_id: McpServer identifier.
            tool_name: Name of the tool to check.
            group_id: Optional group identifier.
            member_id: Optional member identifier.

        Returns:
            True if the tool is allowed, False otherwise.
        """
        return self.is_allowed(mcp_server_id, tool_name, group_id=group_id, member_id=member_id)

    def filter_tools(
        self,
        mcp_server_id: str,
        tools: list[ToolSchema],
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> list[ToolSchema]:
        """Filter tool schemas to only those allowed by policy.

        Args:
            mcp_server_id: McpServer identifier.
            tools: List of ToolSchema objects to filter.
            group_id: Optional group identifier.
            member_id: Optional member identifier.

        Returns:
            List of ToolSchema objects that are allowed by the effective policy.
        """
        policy = self.resolve_effective_policy(mcp_server_id, group_id, member_id)

        if policy.is_unrestricted():
            return tools

        return [t for t in tools if policy.is_tool_allowed(t.name)]

    def filter_tool_dicts(
        self,
        mcp_server_id: str,
        tools: list[dict[str, Any]],
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter tool dictionaries to only those allowed by policy.

        Args:
            mcp_server_id: McpServer identifier.
            tools: List of tool dictionaries with 'name' key.
            group_id: Optional group identifier.
            member_id: Optional member identifier.

        Returns:
            List of tool dictionaries that are allowed by the effective policy.
        """
        policy = self.resolve_effective_policy(mcp_server_id, group_id, member_id)

        if policy.is_unrestricted():
            return tools

        return [t for t in tools if policy.is_tool_allowed(t.get("name", ""))]

    def invalidate_cache(self, mcp_server_id: str | None = None) -> None:
        """Invalidate cached effective policies.

        Called on config reload or policy change.
        If mcp_server_id is None, invalidates all caches.

        Args:
            mcp_server_id: Optional mcp_server_id to invalidate. None invalidates all.
        """
        with self._lock:
            if mcp_server_id is None:
                self._policy_cache.clear()
                logger.debug("tool_access_cache_invalidated_all")
            else:
                self._invalidate_mcp_server_cache(mcp_server_id)
                logger.debug("tool_access_cache_invalidated", extra={"mcp_server_id": mcp_server_id})

    def _invalidate_mcp_server_cache(self, mcp_server_id: str) -> None:
        """Invalidate cache entries related to a mcp_server.

        Must be called with lock held. Every kind is dropped: cheaper than
        tracking which one changed, and a stale entry for another kind is the
        kind of bug that only shows up under a reload.
        """
        # The direct mcp_server cache, plus standalone server→member entries.
        stale = {f"mcp_server:{mcp_server_id}"}
        stale |= {k[1] for k in self._policy_cache if k[1].startswith(f"mcp_server:{mcp_server_id}:member:")}

        # Any group member caches that reference this mcp_server.
        stale |= {
            f"group:{group_id}:member:{member_id}"
            for (group_id, member_id), mapped in self._member_mcp_server_mapping.items()
            if mapped == mcp_server_id
        }

        for key in [k for k in self._policy_cache if k[1] in stale]:
            self._policy_cache.pop(key, None)

    def _invalidate_group_cache(self, group_id: str) -> None:
        """Invalidate cache entries related to a group (all kinds).

        Must be called with lock held.
        """
        for key in [k for k in self._policy_cache if k[1].startswith(f"group:{group_id}:")]:
            self._policy_cache.pop(key, None)

    def get_policy_summary(self, mcp_server_id: str) -> dict[str, Any]:
        """Get a summary of the tool policy for a mcp_server (for observability).

        Args:
            mcp_server_id: McpServer identifier.

        Returns:
            Dictionary with policy status information.
        """
        with self._lock:
            policy = self._mcp_server_policies.get((mcp_server_id, DEFAULT_KIND))
            if policy is None:
                return {
                    "active": False,
                    "unrestricted": True,
                }
            return {
                "active": True,
                "unrestricted": policy.is_unrestricted(),
                "has_allow_list": bool(policy.allow_list),
                "has_deny_list": bool(policy.deny_list),
            }

    def clear_all(self) -> None:
        """Clear all policies, caches, and topology mode (resets to default).

        Useful for testing or complete config reload.
        """
        with self._lock:
            self._policy_cache.clear()
            self._mcp_server_policies.clear()
            self._group_policies.clear()
            self._member_policies.clear()
            self._member_mcp_server_mapping.clear()
            self._standalone_member_policies.clear()
            self._topology_mode = _DEFAULT_MODE


# Global singleton instance
_resolver: ToolAccessResolver | None = None
_resolver_lock = threading.Lock()


def get_tool_access_resolver() -> ToolAccessResolver:
    """Get the global ToolAccessResolver instance.

    Returns:
        The singleton ToolAccessResolver instance.
    """
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = ToolAccessResolver()
    return _resolver


def is_front_door() -> bool:
    """Whether this process is configured as a front door, safely.

    Every caller of `topology_mode` asks the same question and has to survive the
    same failure: a resolver that cannot be reached must not take the process
    down, and "not a front door" is the answer that changes nothing. Two call
    sites had written that out separately -- the flat-handler gate and the
    boot-time warm-up -- and a third would have written it a third time.
    """
    try:
        return get_tool_access_resolver().topology_mode == "front_door"
    except Exception:  # noqa: BLE001 -- an unresolvable topology must not break startup
        logger.warning("topology_mode_unresolved", exc_info=True)
        return False


def reset_tool_access_resolver() -> None:
    """Reset the global ToolAccessResolver instance.

    Useful for testing.
    """
    global _resolver
    with _resolver_lock:
        if _resolver is not None:
            _resolver.clear_all()
        _resolver = None
