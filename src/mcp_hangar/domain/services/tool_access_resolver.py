"""Tool access resolver domain service: the policies, and their registration.

Holds every registered access policy, per mcp_server, group, member and tenant,
and the cache of what they merge to. What a name is decided to be is not asked
here: ``decide()`` in ``access_decision.py`` is the one entry point, and the
merge it reads through is ``_policy_merge``, private to this package (#1784).

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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal, cast

from ..exceptions import ConfigurationError
from ..value_objects import ToolAccessPolicy
from ..value_objects.access import DEFAULT_KIND, PolicyKind

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


def configured_topology_mode(config: Mapping[str, Any]) -> TopologyMode:
    """The topology a configuration asks for in ``tool_access.mode``.

    Startup applies what this returns, and a reload compares it with the
    running mode: the front-door tool surface is built at boot, so a reload
    refuses a mode it cannot follow rather than applying half of it.

    An ABSENT key still means "egress": a deployment that never opted in must
    not be silently switched to the fail-closed topology by an upgrade.

    An UNRECOGNISED value is a hard error. An operator who wrote
    ``mode: front-door`` (hyphen) or ``mode: frontdoor`` was configuring this
    deliberately, and quietly resolving their typo to ``egress`` hands them the
    permissive topology while their config file says otherwise -- a warning in
    the log is not a fair trade for that.

    Raises:
        ConfigurationError: If tool_access.mode is present but not a valid mode.
    """
    tool_access_config = config.get("tool_access", {})
    raw_mode = tool_access_config.get("mode") if isinstance(tool_access_config, dict) else None

    if raw_mode is None:
        return _DEFAULT_MODE
    if raw_mode in ("egress", "front_door"):
        return cast(TopologyMode, raw_mode)
    raise ConfigurationError(
        f"Invalid tool_access.mode {raw_mode!r}. Valid values are 'egress' and 'front_door'; "
        "omit the key entirely to keep the default 'egress' topology."
    )


# What a policy governs -- `PolicyKind` and `DEFAULT_KIND` -- is defined with
# `Target` in `value_objects/access.py` (#1784) and imported above; the names
# stay importable from here.


def _scope_label(scope: str, kind: str) -> str:
    """Describe a registered scope, naming the kind only when it is not tool."""
    return scope if kind == DEFAULT_KIND else f"{scope}[{kind}]"


#: The policy tables, by the name provenance is tracked under. Each maps a key
#: to a ToolAccessPolicy; the key shape differs per table (see ``__init__``).
_POLICY_TABLES = ("mcp_server", "group", "member", "standalone_member")


def _scope_covers(scope_key: str, prefix: str) -> bool:
    """Is *scope_key* the scope named by *prefix*, or a narrower one under it?

    Cache scope keys gained a trailing ``:tenant:{tenant_id}`` segment when the
    group branch started resolving the member server and the tenant separately
    (#1164), so an invalidation that matched a scope key exactly now has to
    match its narrower forms too. Segment-anchored: ``group:g:member:m`` never
    matches ``group:g:member:m2``.
    """
    return scope_key == prefix or scope_key.startswith(f"{prefix}:")


class ToolAccessResolver:
    """The registered access policies for every mcp_server, group and member.

    The registration API is public. Reading the merge of them is not: that is
    ``decide()``, through ``_policy_merge`` (#1784).
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

        # Which entries the configuration file put there, per table, as opposed
        # to a runtime caller: the REST policy endpoint, its replay at boot, the
        # agent's `_global` policy, or `hangar_load`. A reload replaces the
        # file's entries and keeps the rest (`adopt_config_policies`). Every
        # public setter is a runtime write and takes its key out of here, so an
        # entry the file set and a runtime caller then overwrote is the
        # caller's. Kind-independent member mappings are tracked the same way.
        self._config_keys: dict[str, set[tuple[str, ...]]] = {table: set() for table in _POLICY_TABLES}
        self._config_mapping_keys: set[tuple[str, str]] = set()

        # Topology mode controls what happens when caller has no identity
        # (member_id is None and no group context).
        # See module-level TopologyMode for semantics.
        self._topology_mode: TopologyMode = _DEFAULT_MODE

    def _tables(self) -> dict[str, dict[Any, ToolAccessPolicy]]:
        """The policy tables by provenance name. Lock must be held."""
        return {
            "mcp_server": self._mcp_server_policies,
            "group": self._group_policies,
            "member": self._member_policies,
            "standalone_member": self._standalone_member_policies,
        }

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
            self._config_keys["mcp_server"].discard((mcp_server_id, kind))
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
            self._config_keys["group"].discard((group_id, kind))
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
            self._config_keys["member"].discard(key)

            if resolved_mcp_server_id:
                self._member_mcp_server_mapping[(group_id, member_id)] = resolved_mcp_server_id
                self._config_mapping_keys.discard((group_id, member_id))

            # Invalidate cache for this member, every tenant it was resolved for.
            self._pop_cache_scope(f"group:{group_id}:member:{member_id}", kind=kind)

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
            self._config_keys["standalone_member"].discard(key)
            # Invalidate cache for this (server, member) pair. Also every group
            # scope: the group branch merges this tenant policy too (#1164), so
            # a group-scoped entry resolved for this tenant is now stale.
            self._pop_cache_scope(f"mcp_server:{mcp_server_id}:member:{member_id}", kind=kind)
            for stale in [k for k in self._policy_cache if k[1].endswith(f":tenant:{member_id}")]:
                self._policy_cache.pop(stale, None)

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
            # str, not PolicyKind: the policy dicts key their kind as a plain
            # str, and narrowing here would only be a cast dressed as a type.
            registered: list[tuple[str, str, ToolAccessPolicy]] = []
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
        """Remove EVERY access policy for a mcp_server -- tool, prompt and resource,
        the server's own AND its per-tenant (``tool_access.member.<tenant>``) ones.

        The one caller is the hot-unload path, and unloading a server retires
        the whole server, not one kind of policy about it. Removing a single
        kind would leave the other two behind for an id that is free to be
        loaded again, so a later server inheriting that id would be governed by
        its predecessor's rules (#1028). The same argument holds one axis over:
        a per-tenant policy left behind is materialised fresh on the next
        resolve, because the cache that might have masked it is correctly
        cleared below (#1138).

        Args:
            mcp_server_id: McpServer identifier.
        """
        with self._lock:
            for key in [k for k in self._mcp_server_policies if k[0] == mcp_server_id]:
                self._mcp_server_policies.pop(key, None)
                self._config_keys["mcp_server"].discard(key)
            for member_key in [k for k in self._standalone_member_policies if k[0] == mcp_server_id]:
                self._standalone_member_policies.pop(member_key, None)
                self._config_keys["standalone_member"].discard(member_key)
            # Already drops every `mcp_server:<id>:member:` entry too.
            self._invalidate_mcp_server_cache(mcp_server_id)

    def remove_provider_policy(self, provider_id: str) -> None:
        """Legacy alias for remove_mcp_server_policy."""
        self.remove_mcp_server_policy(provider_id)

    def remove_group_policy(self, group_id: str) -> None:
        """Remove EVERY access policy for a group -- tool, prompt and resource.

        Same argument as :meth:`remove_mcp_server_policy`: removing a single
        kind would leave the other two behind for an id that is free to be
        used again (#1138).

        Args:
            group_id: Group identifier.
        """
        with self._lock:
            for key in [k for k in self._group_policies if k[0] == group_id]:
                self._group_policies.pop(key, None)
                self._config_keys["group"].discard(key)
            self._invalidate_group_cache(group_id)

    def remove_member_policy(self, group_id: str, member_id: str) -> None:
        """Remove EVERY access policy for a group member -- tool, prompt and resource.

        Same argument as :meth:`remove_mcp_server_policy`: removing a single
        kind would leave the other two behind for an id that is free to be
        used again (#1138).

        Args:
            group_id: Group identifier.
            member_id: Member identifier.
        """
        with self._lock:
            for key in [k for k in self._member_policies if k[:2] == (group_id, member_id)]:
                self._member_policies.pop(key, None)
                self._config_keys["member"].discard(key)
                self._pop_cache_scope(f"group:{group_id}:member:{member_id}", kind=cast(PolicyKind, key[2]))
            self._member_mcp_server_mapping.pop((group_id, member_id), None)
            self._config_mapping_keys.discard((group_id, member_id))

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

    def _pop_cache_scope(self, prefix: str, *, kind: PolicyKind | None = None) -> None:
        """Drop every cache entry at *prefix* or narrower. Lock must be held."""
        for key in [k for k in self._policy_cache if (kind is None or k[0] == kind) and _scope_covers(k[1], prefix)]:
            self._policy_cache.pop(key, None)

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

        for key in [k for k in self._policy_cache if any(_scope_covers(k[1], s) for s in stale)]:
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
        """Clear every policy and the cache. The topology mode is kept.

        It used to reset the mode too, and the reload handler called it: every
        reload quietly turned a `front_door` gateway into an `egress` one, while
        the tool surface built at boot still looked like a front door (#1424).
        The mode is a boot decision; :meth:`reset` is the full reset.
        """
        with self._lock:
            self._policy_cache.clear()
            self._mcp_server_policies.clear()
            self._group_policies.clear()
            self._member_policies.clear()
            self._member_mcp_server_mapping.clear()
            self._standalone_member_policies.clear()
            for keys in self._config_keys.values():
                keys.clear()
            self._config_mapping_keys.clear()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the resolver's lock across several steps, so a concurrent resolve sees none or all of them.

        A reload adopts the file's policies and then replays the stored runtime
        ones over them (#1424); between the two, a scope both define would
        otherwise resolve to the file's policy for a moment.
        """
        with self._lock:
            yield

    def reset(self) -> None:
        """Put the resolver back as it was built: no policies, and the default mode."""
        with self._lock:
            self.clear_all()
            self._topology_mode = _DEFAULT_MODE

    def adopt_config_policies(self, staged: "ToolAccessResolver", *, replace: bool) -> None:
        """Take the policies a configuration registered on *staged*, in one step.

        The configuration registers its policies on a fresh resolver, and they
        are taken from it here under this resolver's lock. A concurrent resolve
        therefore sees the previous set or the new one, never the empty set a
        reload used to leave between clearing everything and registering it
        again (#1424).

        With *replace*, which is what a reload asks for, an entry the previous
        configuration set and this one does not is removed, so deleting a policy
        from the file lifts it. An entry a runtime caller set -- the REST policy
        endpoint, its replay at boot, the agent's `_global` policy, `hangar_load`
        -- is kept, unless *staged* defines the same scope: then the file's
        policy replaces it. Without *replace*, the file's entries are added to
        what is there, which is what a first load does.

        The topology mode is not taken from *staged*. It is set at startup, and
        a reload refuses a configuration that changes it.
        """
        with staged._lock:
            incoming = {table: dict(entries) for table, entries in staged._tables().items()}
            incoming_mapping = dict(staged._member_mcp_server_mapping)
        with self._lock:
            live_tables = self._tables()
            for table, entries in incoming.items():
                live = live_tables[table]
                if replace:
                    for key in self._config_keys[table] - set(entries):
                        live.pop(key, None)
                    self._config_keys[table] = set(entries)
                else:
                    self._config_keys[table] |= set(entries)
                live.update(entries)
            if replace:
                for mapping_key in self._config_mapping_keys - set(incoming_mapping):
                    self._member_mcp_server_mapping.pop(mapping_key, None)
                self._config_mapping_keys = set(incoming_mapping)
            else:
                self._config_mapping_keys |= set(incoming_mapping)
            self._member_mcp_server_mapping.update(incoming_mapping)
            self._policy_cache.clear()


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
            _resolver.reset()
        _resolver = None
