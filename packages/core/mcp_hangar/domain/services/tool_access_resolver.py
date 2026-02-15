"""Tool access resolver domain service.

Resolves effective tool access policy for any provider/group/member combination.
Handles the three-level merge: provider -> group -> member.
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
    """Resolves effective tool access policy for any provider/group/member combination.

    Handles the three-level merge: provider -> group -> member.
    Caches effective policies per-member for performance.
    Invalidates cache on config reload.

    Thread-safe: uses RLock for cache access.
    """

    def __init__(self) -> None:
        """Initialize the resolver with empty caches."""
        self._lock = threading.RLock()
        # Cache key format:
        # - "provider:{provider_id}" for standalone providers
        # - "group:{group_id}:member:{member_id}" for group members
        self._policy_cache: dict[str, ToolAccessPolicy] = {}

        # Policy sources - set by external config loader
        # Maps provider_id -> ToolAccessPolicy
        self._provider_policies: dict[str, ToolAccessPolicy] = {}
        # Maps group_id -> ToolAccessPolicy
        self._group_policies: dict[str, ToolAccessPolicy] = {}
        # Maps (group_id, member_id) -> ToolAccessPolicy
        self._member_policies: dict[tuple[str, str], ToolAccessPolicy] = {}
        # Maps (group_id, member_id) -> provider_id (for resolving member's provider)
        self._member_provider_mapping: dict[tuple[str, str], str] = {}

    def set_provider_policy(self, provider_id: str, policy: ToolAccessPolicy) -> None:
        """Set the tool access policy for a provider.

        Args:
            provider_id: Provider identifier.
            policy: Tool access policy to apply.
        """
        with self._lock:
            if policy.is_unrestricted():
                self._provider_policies.pop(provider_id, None)
            else:
                self._provider_policies[provider_id] = policy
            # Invalidate cache for this provider
            self._invalidate_provider_cache(provider_id)

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
        provider_id: str | None = None,
    ) -> None:
        """Set the tool access policy for a specific group member.

        Args:
            group_id: Group identifier.
            member_id: Member identifier within the group.
            policy: Tool access policy for this member.
            provider_id: The provider_id this member maps to (for policy inheritance).
        """
        key = (group_id, member_id)
        with self._lock:
            if policy.is_unrestricted():
                self._member_policies.pop(key, None)
            else:
                self._member_policies[key] = policy

            if provider_id:
                self._member_provider_mapping[key] = provider_id

            # Invalidate cache for this member
            cache_key = f"group:{group_id}:member:{member_id}"
            self._policy_cache.pop(cache_key, None)

    def remove_provider_policy(self, provider_id: str) -> None:
        """Remove tool access policy for a provider.

        Args:
            provider_id: Provider identifier.
        """
        with self._lock:
            self._provider_policies.pop(provider_id, None)
            self._invalidate_provider_cache(provider_id)

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
            self._member_provider_mapping.pop(key, None)
            cache_key = f"group:{group_id}:member:{member_id}"
            self._policy_cache.pop(cache_key, None)

    def resolve_effective_policy(
        self,
        provider_id: str,
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> ToolAccessPolicy:
        """Get the effective tool access policy for a specific context.

        For standalone providers: returns provider-level policy.
        For group members: merges provider -> group -> member policies.

        Args:
            provider_id: Provider identifier.
            group_id: Optional group identifier (for group member context).
            member_id: Optional member identifier (for group member context).

        Returns:
            The effective ToolAccessPolicy for this context.
        """
        # Build cache key
        if group_id and member_id:
            cache_key = f"group:{group_id}:member:{member_id}"
        else:
            cache_key = f"provider:{provider_id}"

        # Check cache first
        with self._lock:
            if cache_key in self._policy_cache:
                return self._policy_cache[cache_key]

            # Compute effective policy
            effective = self._compute_effective_policy(provider_id, group_id, member_id)

            # Cache it
            self._policy_cache[cache_key] = effective
            return effective

    def _compute_effective_policy(
        self,
        provider_id: str,
        group_id: str | None,
        member_id: str | None,
    ) -> ToolAccessPolicy:
        """Compute effective policy by merging all applicable levels.

        Must be called with lock held.
        """
        # Start with provider policy
        provider_policy = self._provider_policies.get(provider_id, ToolAccessPolicy())

        # If no group context, just return provider policy
        if not group_id or not member_id:
            return provider_policy

        # Get group policy
        group_policy = self._group_policies.get(group_id, ToolAccessPolicy())

        # Get member policy
        member_key = (group_id, member_id)
        member_policy = self._member_policies.get(member_key, ToolAccessPolicy())

        # If member maps to a different provider, also get that provider's policy
        mapped_provider_id = self._member_provider_mapping.get(member_key)
        if mapped_provider_id and mapped_provider_id != provider_id:
            mapped_provider_policy = self._provider_policies.get(mapped_provider_id, ToolAccessPolicy())
            # Merge mapped provider policy with base provider policy
            provider_policy = ToolAccessPolicy.merge(provider_policy, mapped_provider_policy)

        # Three-level merge: provider -> group -> member
        step1 = ToolAccessPolicy.merge(provider_policy, group_policy)
        step2 = ToolAccessPolicy.merge(step1, member_policy)

        return step2

    def is_tool_allowed(
        self,
        provider_id: str,
        tool_name: str,
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> bool:
        """Quick check if a specific tool is allowed in context.

        Args:
            provider_id: Provider identifier.
            tool_name: Name of the tool to check.
            group_id: Optional group identifier.
            member_id: Optional member identifier.

        Returns:
            True if the tool is allowed, False otherwise.
        """
        policy = self.resolve_effective_policy(provider_id, group_id, member_id)
        return policy.is_tool_allowed(tool_name)

    def filter_tools(
        self,
        provider_id: str,
        tools: list[ToolSchema],
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> list[ToolSchema]:
        """Filter tool schemas to only those allowed by policy.

        Args:
            provider_id: Provider identifier.
            tools: List of ToolSchema objects to filter.
            group_id: Optional group identifier.
            member_id: Optional member identifier.

        Returns:
            List of ToolSchema objects that are allowed by the effective policy.
        """
        policy = self.resolve_effective_policy(provider_id, group_id, member_id)

        if policy.is_unrestricted():
            return tools

        return [t for t in tools if policy.is_tool_allowed(t.name)]

    def filter_tool_dicts(
        self,
        provider_id: str,
        tools: list[dict[str, Any]],
        group_id: str | None = None,
        member_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter tool dictionaries to only those allowed by policy.

        Args:
            provider_id: Provider identifier.
            tools: List of tool dictionaries with 'name' key.
            group_id: Optional group identifier.
            member_id: Optional member identifier.

        Returns:
            List of tool dictionaries that are allowed by the effective policy.
        """
        policy = self.resolve_effective_policy(provider_id, group_id, member_id)

        if policy.is_unrestricted():
            return tools

        return [t for t in tools if policy.is_tool_allowed(t.get("name", ""))]

    def invalidate_cache(self, provider_id: str | None = None) -> None:
        """Invalidate cached effective policies.

        Called on config reload or policy change.
        If provider_id is None, invalidates all caches.

        Args:
            provider_id: Optional provider_id to invalidate. None invalidates all.
        """
        with self._lock:
            if provider_id is None:
                self._policy_cache.clear()
                logger.debug("tool_access_cache_invalidated_all")
            else:
                self._invalidate_provider_cache(provider_id)
                logger.debug("tool_access_cache_invalidated", provider_id=provider_id)

    def _invalidate_provider_cache(self, provider_id: str) -> None:
        """Invalidate cache entries related to a provider.

        Must be called with lock held.
        """
        # Remove direct provider cache
        cache_key = f"provider:{provider_id}"
        self._policy_cache.pop(cache_key, None)

        # Remove any member caches that reference this provider
        keys_to_remove = []
        for member_key, mapped_provider in self._member_provider_mapping.items():
            if mapped_provider == provider_id:
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

    def get_policy_summary(self, provider_id: str) -> dict[str, Any]:
        """Get a summary of the policy for a provider (for observability).

        Args:
            provider_id: Provider identifier.

        Returns:
            Dictionary with policy status information.
        """
        with self._lock:
            policy = self._provider_policies.get(provider_id)
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
            self._provider_policies.clear()
            self._group_policies.clear()
            self._member_policies.clear()
            self._member_provider_mapping.clear()


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
