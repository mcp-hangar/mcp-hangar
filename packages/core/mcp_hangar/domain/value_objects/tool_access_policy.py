"""Tool access policy value object for controlling tool visibility and invocation.

This module implements config-driven tool filtering that controls which tools
are visible and invocable per provider, per group, and per group member.
This is Phase 0 of governance - static, identity-agnostic policy that becomes
the foundation RBAC builds on top of.

Resolution semantics:
- If allow_list is defined, ONLY matching tools are visible (deny_list ignored)
- If only deny_list is defined, all tools EXCEPT matching are visible
- If both are empty, all tools are visible (no filtering)
- Supports fnmatch glob patterns (e.g., 'delete_*', '*_alert_*')

Merge semantics (for scope resolution):
- Each level can only REMOVE tools, never add back tools that a broader scope removed
- Security flows downhill: provider -> group -> member
"""

from dataclasses import dataclass, field
from fnmatch import fnmatch
from collections.abc import Callable


def _matches_any_pattern(name: str, patterns: tuple[str, ...]) -> bool:
    """Check if name matches any of the given patterns."""
    return any(fnmatch(name, pattern) for pattern in patterns)


@dataclass(frozen=True)
class ToolAccessPolicy:
    """Immutable policy defining which tools are accessible.

    If allow_list is non-empty, only matching tools are visible.
    If allow_list is empty AND deny_list is non-empty, matching tools are hidden.
    If both are empty, all tools are visible (no filtering).

    allow_list takes precedence over deny_list when both are defined.
    Supports fnmatch glob patterns (e.g., 'delete_*', '*_alert_*').

    Attributes:
        allow_list: Patterns for tools to allow. If non-empty, only these are visible.
        deny_list: Patterns for tools to deny. Only used when allow_list is empty.
    """

    allow_list: tuple[str, ...] = field(default_factory=tuple)
    deny_list: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate patterns are valid strings."""
        for pattern in self.allow_list:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"Invalid allow_list pattern: {pattern!r}")
        for pattern in self.deny_list:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"Invalid deny_list pattern: {pattern!r}")

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a specific tool is allowed by this policy.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            True if the tool is allowed, False otherwise.
        """
        if self.allow_list:
            return _matches_any_pattern(tool_name, self.allow_list)
        if self.deny_list:
            return not _matches_any_pattern(tool_name, self.deny_list)
        return True

    def filter_tools(self, tool_names: list[str]) -> list[str]:
        """Return only the tools allowed by this policy.

        Args:
            tool_names: List of tool names to filter.

        Returns:
            List of tool names that are allowed by this policy.
        """
        return [name for name in tool_names if self.is_tool_allowed(name)]

    def is_unrestricted(self) -> bool:
        """Check if this policy allows all tools.

        Returns:
            True if no filtering is applied (both lists empty).
        """
        return not self.allow_list and not self.deny_list

    @staticmethod
    def merge(broader: "ToolAccessPolicy", narrower: "ToolAccessPolicy") -> "ToolAccessPolicy":
        """Merge two policies where narrower scope can only restrict further.

        This is NOT a simple override. Each level can only remove tools,
        never add back what a broader scope removed.

        The invariant that must hold:
            merged.filter_tools(tools) == narrower.filter_tools(broader.filter_tools(tools))
            for ALL possible tool lists

        Args:
            broader: The broader scope policy (e.g., provider-level).
            narrower: The narrower scope policy (e.g., group-level or member-level).

        Returns:
            A single effective ToolAccessPolicy that represents the combined effect.
        """
        # Case 1: Narrower defines nothing -> broader passes through
        if narrower.is_unrestricted():
            return broader

        # Case 2: Broader defines nothing -> narrower becomes effective
        if broader.is_unrestricted():
            return narrower

        # For composite policies, delegate to their merge implementation
        if isinstance(broader, _CompositePolicy):
            return broader.merge_with(narrower)

        # Case 3: Both have allow_lists -> intersection
        if broader.allow_list and narrower.allow_list:
            return _CompositePolicy(
                check=lambda name: (
                    _matches_any_pattern(name, broader.allow_list) and _matches_any_pattern(name, narrower.allow_list)
                ),
                description=f"allow_intersection({list(broader.allow_list)} & {list(narrower.allow_list)})",
            )

        # Case 4: Broader has allow_list, narrower has deny_list
        if broader.allow_list and narrower.deny_list:
            return _CompositePolicy(
                check=lambda name: (
                    _matches_any_pattern(name, broader.allow_list)
                    and not _matches_any_pattern(name, narrower.deny_list)
                ),
                description=f"allow({list(broader.allow_list)}) - deny({list(narrower.deny_list)})",
            )

        # Case 5: Broader has deny_list, narrower has allow_list
        if broader.deny_list and narrower.allow_list:
            return _CompositePolicy(
                check=lambda name: (
                    not _matches_any_pattern(name, broader.deny_list)
                    and _matches_any_pattern(name, narrower.allow_list)
                ),
                description=f"not_deny({list(broader.deny_list)}) & allow({list(narrower.allow_list)})",
            )

        # Case 6: Both have deny_lists -> union of denials
        if broader.deny_list and narrower.deny_list:
            combined_deny = tuple(set(broader.deny_list) | set(narrower.deny_list))
            return ToolAccessPolicy(deny_list=combined_deny)

        # Fallback
        return narrower

    def __repr__(self) -> str:
        if self.is_unrestricted():
            return "ToolAccessPolicy(unrestricted)"
        if self.allow_list:
            return f"ToolAccessPolicy(allow={list(self.allow_list)})"
        return f"ToolAccessPolicy(deny={list(self.deny_list)})"


@dataclass(frozen=True)
class _CompositePolicy(ToolAccessPolicy):
    """Internal policy representing a composite of multiple merge operations.

    Uses a callable check function to determine if a tool is allowed.
    This allows arbitrary composition of policies without losing the merge semantics.
    """

    _check: Callable[[str], bool] | None = field(default=None, repr=False)
    _description: str = field(default="composite")

    def __init__(
        self,
        check: Callable[[str], bool],
        description: str = "composite",
    ) -> None:
        object.__setattr__(self, "allow_list", ())
        object.__setattr__(self, "deny_list", ())
        object.__setattr__(self, "_check", check)
        object.__setattr__(self, "_description", description)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check using the composite function."""
        if self._check is not None:
            return self._check(tool_name)
        return True

    def is_unrestricted(self) -> bool:
        """Composite policies are never unrestricted."""
        return False

    def merge_with(self, narrower: "ToolAccessPolicy") -> "ToolAccessPolicy":
        """Merge this composite policy with a narrower policy.

        The composite's check becomes the broader check, and we add
        the narrower's restrictions on top.
        """
        if narrower.is_unrestricted():
            return self

        base_check = self._check

        if narrower.allow_list:
            # Narrower has allow_list: must pass base check AND match allow
            return _CompositePolicy(
                check=lambda name: (
                    (base_check(name) if base_check else True) and _matches_any_pattern(name, narrower.allow_list)
                ),
                description=f"{self._description} & allow({list(narrower.allow_list)})",
            )

        if narrower.deny_list:
            # Narrower has deny_list: must pass base check AND not match deny
            return _CompositePolicy(
                check=lambda name: (
                    (base_check(name) if base_check else True) and not _matches_any_pattern(name, narrower.deny_list)
                ),
                description=f"{self._description} - deny({list(narrower.deny_list)})",
            )

        # Narrower is also a composite - chain them
        if isinstance(narrower, _CompositePolicy) and narrower._check:
            narrower_check = narrower._check
            return _CompositePolicy(
                check=lambda name: ((base_check(name) if base_check else True) and narrower_check(name)),
                description=f"{self._description} & {narrower._description}",
            )

        return self

    def __repr__(self) -> str:
        return f"ToolAccessPolicy({self._description})"
