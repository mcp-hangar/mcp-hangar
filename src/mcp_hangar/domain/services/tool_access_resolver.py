"""Tool access resolver domain service.

Resolves effective tool access policy for any mcp_server/group/member combination.
Handles the three-level merge: mcp_server -> group -> member.
Caches effective policies per-member for performance.
"""

import logging
import threading
from typing import TYPE_CHECKING, Any

from ..model.tool_catalog import ToolSchema
from ..value_objects import ToolAccessPolicy

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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
        # Cache key format:
        # - "mcp_server:{mcp_server_id}" for standalone mcp_servers
        # - "group:{group_id}:member:{member_id}" for group members
        self._policy_cache: dict[str, ToolAccessPolicy] = {}

        # Policy sources - set by external config loader
        # Maps mcp_server_id -> ToolAccessPolicy
        self._mcp_server_policies: dict[str, ToolAccessPolicy] = {}
        # Maps group_id -> ToolAccessPolicy
        self._group_policies: dict[str, ToolAccessPolicy] = {}
        # Maps (group_id, member_id) -> ToolAccessPolicy
        self._member_policies: dict[tuple[str, str], ToolAccessPolicy] = {}
        # Maps (group_id, member_id) -> mcp_server_id (for resolving member's mcp_server)
        self._member_mcp_server_mapping: dict[tuple[str, str], str] = {}

    def set_mcp_server_policy(self, mcp_server_id: str, policy: ToolAccessPolicy) -> None:
        """Set the tool access policy for a mcp_server.

        Args:
            mcp_server_id: McpServer identifier.
            policy: Tool access policy to apply.
        """
        with self._lock:
            if policy.is_unrestricted():
                self._mcp_server_policies.pop(mcp_server_id, None)
            else:
                self._mcp_server_policies[mcp_server_id] = policy
            # Invalidate cache for this mcp_server
            self._invalidate_mcp_server_cache(mcp_server_id)

    def set_provider_policy(self, provider_id: str, policy: ToolAccessPolicy) -> None:
        """Legacy alias for set_mcp_server_policy."""
        self.set_mcp_server_policy(provider_id, policy)

    def set_group_policy(self, group_id: str, policy: ToolAccessPolicy) -> None:
        """Set the tool access policy for a group.

        Args:
            group_id: Group identifier.
            policy: Tool access policy to apply to all members.
        """
        with self._lock:
            if policy.is_unrestricted():
                self._group_policies.pop(group_id, None)
            else:
                self._group_policies[group_id] = policy
            # Invalidate cache for all members in this group
            self._invalidate_group_cache(group_id)

    def set_member_policy(
        self,
        group_id: str,
        member_id: str,
        policy: ToolAccessPolicy,
        mcp_server_id: str | None = None,
        provider_id: str | None = None,
    ) -> None:
        """Set the tool access policy for a specific group member.

        Args:
            group_id: Group identifier.
            member_id: Member identifier within the group.
            policy: Tool access policy for this member.
            mcp_server_id: The mcp_server_id this member maps to (for policy inheritance).
        """
        resolved_mcp_server_id = mcp_server_id or provider_id
        key = (group_id, member_id)
        with self._lock:
            if policy.is_unrestricted():
                self._member_policies.pop(key, None)
            else:
                self._member_policies[key] = policy

            if resolved_mcp_server_id:
                self._member_mcp_server_mapping[key] = resolved_mcp_server_id

            # Invalidate cache for this member
            cache_key = f"group:{group_id}:member:{member_id}"
            self._policy_cache.pop(cache_key, None)

    def remove_mcp_server_policy(self, mcp_server_id: str) -> None:
        """Remove tool access policy for a mcp_server.

        Args:
            mcp_server_id: McpServer identifier.
        """
        with self._lock:
            self._mcp_server_policies.pop(mcp_server_id, None)
            self._invalidate_mcp_server_cache(mcp_server_id)

    def remove_provider_policy(self, provider_id: str) -> None:
        """Legacy alias for remove_mcp_server_policy."""
        self.remove_mcp_server_policy(provider_id)

    def remove_group_policy(self, group_id: str) -> None:
        """Remove tool access policy for a group.

        Args:
            group_id: Group identifier.
        """
        with self._lock:
            self._group_policies.pop(group_id, None)
            self._invalidate_group_cache(group_id)

    def remove_member_policy(self, group_id: str, member_id: str) -> None:
        """Remove tool access policy for a group member.

        Args:
            group_id: Group identifier.
            member_id: Member identifier.
        """
        key = (group_id, member_id)
        with self._lock:
            self._member_policies.pop(key, None)
            self._member_mcp_server_mapping.pop(key, None)
            cache_key = f"group:{group_id}:member:{member_id}"
            self._policy_cache.pop(cache_key, None)

    def resolve_effective_policy(
        self,
        mcp_server_id: str,
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> ToolAccessPolicy:
        """Get the effective tool access policy for a specific context.

        For standalone mcp_servers: returns mcp_server-level policy.
        For group members: merges mcp_server -> group -> member policies.

        Args:
            mcp_server_id: McpServer identifier.
            group_id: Optional group identifier (for group member context).
            member_id: Optional member identifier (for group member context).

        Returns:
            The effective ToolAccessPolicy for this context.
        """
        # Build cache key
        if group_id and member_id:
            cache_key = f"group:{group_id}:member:{member_id}"
        else:
            cache_key = f"mcp_server:{mcp_server_id}"

        # Check cache first
        with self._lock:
            if cache_key in self._policy_cache:
                return self._policy_cache[cache_key]

            # Compute effective policy
            effective = self._compute_effective_policy(mcp_server_id, group_id, member_id)

            # Cache it
            self._policy_cache[cache_key] = effective
            return effective

    def _compute_effective_policy(
        self,
        mcp_server_id: str,
        group_id: str | None,
        member_id: str | None,
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
        explicit_mcp_server_policy = self._mcp_server_policies.get(mcp_server_id)
        global_policy = self._mcp_server_policies.get("_global", ToolAccessPolicy())

        if explicit_mcp_server_policy is None:
            mcp_server_policy = global_policy
        else:
            mcp_server_policy = ToolAccessPolicy.merge(global_policy, explicit_mcp_server_policy)

        # If no group context, just return mcp_server policy
        if not group_id or not member_id:
            return mcp_server_policy

        # Get group policy
        group_policy = self._group_policies.get(group_id, ToolAccessPolicy())

        # Get member policy
        member_key = (group_id, member_id)
        member_policy = self._member_policies.get(member_key, ToolAccessPolicy())

        # If member maps to a different mcp_server, also get that mcp_server's policy
        mapped_mcp_server_id = self._member_mcp_server_mapping.get(member_key)
        if mapped_mcp_server_id and mapped_mcp_server_id != mcp_server_id:
            mapped_mcp_server_policy = self._mcp_server_policies.get(mapped_mcp_server_id, ToolAccessPolicy())
            # Merge mapped mcp_server policy with base mcp_server policy
            mcp_server_policy = ToolAccessPolicy.merge(mcp_server_policy, mapped_mcp_server_policy)

        # Three-level merge: mcp_server -> group -> member
        step1 = ToolAccessPolicy.merge(mcp_server_policy, group_policy)
        step2 = ToolAccessPolicy.merge(step1, member_policy)

        return step2

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
        policy = self.resolve_effective_policy(mcp_server_id, group_id, member_id)
        return policy.is_tool_allowed(tool_name)

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
                logger.debug("tool_access_cache_invalidated", mcp_server_id=mcp_server_id)

    def _invalidate_mcp_server_cache(self, mcp_server_id: str) -> None:
        """Invalidate cache entries related to a mcp_server.

        Must be called with lock held.
        """
        # Remove direct mcp_server cache
        cache_key = f"mcp_server:{mcp_server_id}"
        self._policy_cache.pop(cache_key, None)

        # Remove any member caches that reference this mcp_server
        keys_to_remove = []
        for member_key, mapped_mcp_server in self._member_mcp_server_mapping.items():
            if mapped_mcp_server == mcp_server_id:
                group_id, member_id = member_key
                keys_to_remove.append(f"group:{group_id}:member:{member_id}")

        for key in keys_to_remove:
            self._policy_cache.pop(key, None)

    def _invalidate_group_cache(self, group_id: str) -> None:
        """Invalidate cache entries related to a group.

        Must be called with lock held.
        """
        keys_to_remove = [k for k in self._policy_cache if k.startswith(f"group:{group_id}:")]
        for key in keys_to_remove:
            self._policy_cache.pop(key, None)

    def get_policy_summary(self, mcp_server_id: str) -> dict[str, Any]:
        """Get a summary of the policy for a mcp_server (for observability).

        Args:
            mcp_server_id: McpServer identifier.

        Returns:
            Dictionary with policy status information.
        """
        with self._lock:
            policy = self._mcp_server_policies.get(mcp_server_id)
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
        """Clear all policies and caches.

        Useful for testing or complete config reload.
        """
        with self._lock:
            self._policy_cache.clear()
            self._mcp_server_policies.clear()
            self._group_policies.clear()
            self._member_policies.clear()
            self._member_mcp_server_mapping.clear()


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


def reset_tool_access_resolver() -> None:
    """Reset the global ToolAccessResolver instance.

    Useful for testing.
    """
    global _resolver
    with _resolver_lock:
        if _resolver is not None:
            _resolver.clear_all()
        _resolver = None
