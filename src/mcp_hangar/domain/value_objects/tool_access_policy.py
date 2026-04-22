"""Tool access policy value object for controlling tool visibility and invocation.

This module implements config-driven tool filtering that controls which tools
are visible and invocable per mcp_server, per group, and per group member.
This is Phase 0 of governance - static, identity-agnostic policy that becomes
the foundation RBAC builds on top of.

Resolution semantics:
- deny_list blocks tools entirely (highest priority)
- approval_list marks tools as visible but held before execution
- allow_list permits tools for immediate execution
- If both allow_list and deny_list are empty, all tools are visible (no filtering)
- Supports fnmatch glob patterns (e.g., 'delete_*', '*_alert_*')

Precedence (high to low):
  deny_list > approval_list > allow_list > unrestricted default

Merge semantics (for scope resolution):
- Each level can only REMOVE tools, never add back tools that a broader scope removed
- approval_list can only grow across scopes, never shrink
- Security flows downhill: mcp_server -> group -> member
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

    Precedence (high to low):
      deny_list > approval_list > allow_list > unrestricted default

    A tool on deny_list is blocked -- approval cannot override deny.
    A tool on approval_list is visible but held before execution.
    A tool on allow_list (or unrestricted) executes immediately.

    is_tool_allowed() returns True for approval_list tools (they are permitted,
    just gated). Callers that only need visibility use this unchanged.

    Supports fnmatch glob patterns (e.g., 'delete_*', '*_alert_*').

    Attributes:
        allow_list: Patterns for tools to allow. If non-empty, only these are visible.
        deny_list: Patterns for tools to deny. Overrides both allow and approval.
        approval_list: Patterns for tools requiring human approval before execution.
        approval_timeout_seconds: Seconds to wait for approval decision.
        approval_channel: Delivery channel for approval notifications.
    """

    allow_list: tuple[str, ...] = field(default_factory=tuple)
    deny_list: tuple[str, ...] = field(default_factory=tuple)
    approval_list: tuple[str, ...] = field(default_factory=tuple)
    approval_timeout_seconds: int = 300
    approval_channel: str = "dashboard"

    def __post_init__(self) -> None:
        """Validate patterns are valid strings."""
        for pattern in self.allow_list:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"Invalid allow_list pattern: {pattern!r}")
        for pattern in self.deny_list:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"Invalid deny_list pattern: {pattern!r}")
        for pattern in self.approval_list:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"Invalid approval_list pattern: {pattern!r}")

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a specific tool is allowed by this policy.

        A tool on deny_list is blocked. A tool on approval_list is allowed
        (visible) but requires approval before execution. A tool on allow_list
        executes immediately.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            True if the tool is allowed (including approval-gated), False otherwise.
        """
        if self.deny_list and _matches_any_pattern(tool_name, self.deny_list):
            return False
        if self.approval_list and _matches_any_pattern(tool_name, self.approval_list):
            return True
        if self.allow_list:
            return _matches_any_pattern(tool_name, self.allow_list)
        return True

    def requires_approval(self, tool_name: str) -> bool:
        """Returns True if tool is allowed but requires human approval before execution.

        A tool on deny_list returns False here (it's not allowed at all).
        A tool on approval_list returns True.
        A tool on allow_list or unrestricted returns False (no approval needed).
        """
        if _matches_any_pattern(tool_name, self.deny_list):
            return False
        return _matches_any_pattern(tool_name, self.approval_list)

    def filter_tools(self, tool_names: list[str]) -> list[str]:
        """Return only the tools allowed by this policy.

        Args:
            tool_names: List of tool names to filter.

        Returns:
            List of tool names that are allowed by this policy.
        """
        return [name for name in tool_names if self.is_tool_allowed(name)]

    def is_unrestricted(self) -> bool:
        """Check if this policy allows all tools without restrictions.

        Returns:
            True if no filtering is applied (all three lists empty).
        """
        return not self.allow_list and not self.deny_list and not self.approval_list

    @staticmethod
    def merge(broader: "ToolAccessPolicy", narrower: "ToolAccessPolicy") -> "ToolAccessPolicy":
        """Merge two policies where narrower scope can only restrict further.

        This is NOT a simple override. Each level can only remove tools,
        never add back what a broader scope removed. approval_list can only
        grow (union) across scopes, never shrink.

        The invariant that must hold:
            merged.filter_tools(tools) == narrower.filter_tools(broader.filter_tools(tools))
            for ALL possible tool lists

        Args:
            broader: The broader scope policy (e.g., mcp_server-level).
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

        # Merge approval_lists: union (more tools require approval)
        merged_approval = tuple(set(broader.approval_list) | set(narrower.approval_list))

        # For composite policies, delegate to their merge implementation
        if isinstance(broader, _CompositePolicy):
            result = broader.merge_with(narrower)
            # Overlay merged approval_list onto result
            if merged_approval and isinstance(result, _CompositePolicy):
                result = result._with_approval_list(merged_approval)
            elif merged_approval and isinstance(result, ToolAccessPolicy):
                result = ToolAccessPolicy(
                    allow_list=result.allow_list,
                    deny_list=result.deny_list,
                    approval_list=merged_approval,
                    approval_timeout_seconds=min(broader.approval_timeout_seconds, narrower.approval_timeout_seconds),
                    approval_channel=narrower.approval_channel if narrower.approval_list else broader.approval_channel,
                )
            return result

        # Case 3: Both have allow_lists -> intersection
        if broader.allow_list and narrower.allow_list:
            return _CompositePolicy(
                check=lambda name: (
                    _matches_any_pattern(name, broader.allow_list) and _matches_any_pattern(name, narrower.allow_list)
                ),
                description=f"allow_intersection({list(broader.allow_list)} & {list(narrower.allow_list)})",
                approval_patterns=merged_approval,
                merged_timeout=min(broader.approval_timeout_seconds, narrower.approval_timeout_seconds),
                merged_channel=narrower.approval_channel if narrower.approval_list else broader.approval_channel,
            )

        # Case 4: Broader has allow_list, narrower has deny_list
        if broader.allow_list and narrower.deny_list:
            return _CompositePolicy(
                check=lambda name: (
                    _matches_any_pattern(name, broader.allow_list)
                    and not _matches_any_pattern(name, narrower.deny_list)
                ),
                description=f"allow({list(broader.allow_list)}) - deny({list(narrower.deny_list)})",
                approval_patterns=merged_approval,
                merged_timeout=min(broader.approval_timeout_seconds, narrower.approval_timeout_seconds),
                merged_channel=narrower.approval_channel if narrower.approval_list else broader.approval_channel,
            )

        # Case 5: Broader has deny_list, narrower has allow_list
        if broader.deny_list and narrower.allow_list:
            return _CompositePolicy(
                check=lambda name: (
                    not _matches_any_pattern(name, broader.deny_list)
                    and _matches_any_pattern(name, narrower.allow_list)
                ),
                description=f"not_deny({list(broader.deny_list)}) & allow({list(narrower.allow_list)})",
                approval_patterns=merged_approval,
                merged_timeout=min(broader.approval_timeout_seconds, narrower.approval_timeout_seconds),
                merged_channel=narrower.approval_channel if narrower.approval_list else broader.approval_channel,
            )

        # Case 6: Both have deny_lists -> union of denials
        if broader.deny_list and narrower.deny_list:
            combined_deny = tuple(set(broader.deny_list) | set(narrower.deny_list))
            return ToolAccessPolicy(
                deny_list=combined_deny,
                approval_list=merged_approval,
                approval_timeout_seconds=min(broader.approval_timeout_seconds, narrower.approval_timeout_seconds),
                approval_channel=narrower.approval_channel if narrower.approval_list else broader.approval_channel,
            )

        # Case 7: Only approval_list involved (one or both have only approval_list)
        if broader.approval_list or narrower.approval_list:
            return ToolAccessPolicy(
                allow_list=broader.allow_list or narrower.allow_list,
                deny_list=broader.deny_list or narrower.deny_list,
                approval_list=merged_approval,
                approval_timeout_seconds=min(broader.approval_timeout_seconds, narrower.approval_timeout_seconds),
                approval_channel=narrower.approval_channel if narrower.approval_list else broader.approval_channel,
            )

        # Fallback
        return narrower

    def __repr__(self) -> str:
        if self.is_unrestricted():
            return "ToolAccessPolicy(unrestricted)"
        parts = []
        if self.allow_list:
            parts.append(f"allow={list(self.allow_list)}")
        if self.deny_list:
            parts.append(f"deny={list(self.deny_list)}")
        if self.approval_list:
            parts.append(f"approval={list(self.approval_list)}")
        return f"ToolAccessPolicy({', '.join(parts)})"


@dataclass(frozen=True)
class _CompositePolicy(ToolAccessPolicy):
    """Internal policy representing a composite of multiple merge operations.

    Uses a callable check function to determine if a tool is allowed.
    This allows arbitrary composition of policies without losing the merge semantics.
    """

    _check: Callable[[str], bool] | None = field(default=None, repr=False)
    _description: str = field(default="composite")
    _approval_patterns: tuple[str, ...] = field(default_factory=tuple)
    _merged_timeout: int = field(default=300)
    _merged_channel: str = field(default="dashboard")

    def __init__(
        self,
        check: Callable[[str], bool],
        description: str = "composite",
        approval_patterns: tuple[str, ...] = (),
        merged_timeout: int = 300,
        merged_channel: str = "dashboard",
    ) -> None:
        object.__setattr__(self, "allow_list", ())
        object.__setattr__(self, "deny_list", ())
        object.__setattr__(self, "approval_list", approval_patterns)
        object.__setattr__(self, "approval_timeout_seconds", merged_timeout)
        object.__setattr__(self, "approval_channel", merged_channel)
        object.__setattr__(self, "_check", check)
        object.__setattr__(self, "_description", description)
        object.__setattr__(self, "_approval_patterns", approval_patterns)
        object.__setattr__(self, "_merged_timeout", merged_timeout)
        object.__setattr__(self, "_merged_channel", merged_channel)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check using the composite function, respecting approval_list."""
        if self._check is not None and not self._check(tool_name):
            return False
        if self._approval_patterns and _matches_any_pattern(tool_name, self._approval_patterns):
            return True
        return True

    def requires_approval(self, tool_name: str) -> bool:
        """Check if tool requires approval in composite context."""
        if self._check is not None and not self._check(tool_name):
            return False
        return _matches_any_pattern(tool_name, self._approval_patterns)

    def is_unrestricted(self) -> bool:
        """Composite policies are never unrestricted."""
        return False

    def _with_approval_list(self, approval_patterns: tuple[str, ...]) -> "_CompositePolicy":
        """Return a new composite with the given approval_list overlay."""
        return _CompositePolicy(
            check=self._check or (lambda _: True),
            description=self._description,
            approval_patterns=approval_patterns,
            merged_timeout=self._merged_timeout,
            merged_channel=self._merged_channel,
        )

    def merge_with(self, narrower: "ToolAccessPolicy") -> "ToolAccessPolicy":
        """Merge this composite policy with a narrower policy.

        The composite's check becomes the broader check, and we add
        the narrower's restrictions on top.
        """
        if narrower.is_unrestricted():
            return self

        base_check = self._check
        merged_approval = tuple(set(self._approval_patterns) | set(narrower.approval_list))
        merged_timeout = min(self._merged_timeout, narrower.approval_timeout_seconds)
        merged_channel = narrower.approval_channel if narrower.approval_list else self._merged_channel

        if narrower.allow_list:
            # Narrower has allow_list: must pass base check AND match allow
            return _CompositePolicy(
                check=lambda name: (
                    (base_check(name) if base_check else True) and _matches_any_pattern(name, narrower.allow_list)
                ),
                description=f"{self._description} & allow({list(narrower.allow_list)})",
                approval_patterns=merged_approval,
                merged_timeout=merged_timeout,
                merged_channel=merged_channel,
            )

        if narrower.deny_list:
            # Narrower has deny_list: must pass base check AND not match deny
            return _CompositePolicy(
                check=lambda name: (
                    (base_check(name) if base_check else True) and not _matches_any_pattern(name, narrower.deny_list)
                ),
                description=f"{self._description} - deny({list(narrower.deny_list)})",
                approval_patterns=merged_approval,
                merged_timeout=merged_timeout,
                merged_channel=merged_channel,
            )

        # Narrower is also a composite - chain them
        if isinstance(narrower, _CompositePolicy) and narrower._check:
            narrower_check = narrower._check
            return _CompositePolicy(
                check=lambda name: ((base_check(name) if base_check else True) and narrower_check(name)),
                description=f"{self._description} & {narrower._description}",
                approval_patterns=merged_approval,
                merged_timeout=merged_timeout,
                merged_channel=merged_channel,
            )

        # Narrower has only approval_list
        if narrower.approval_list:
            return _CompositePolicy(
                check=base_check or (lambda _: True),
                description=self._description,
                approval_patterns=merged_approval,
                merged_timeout=merged_timeout,
                merged_channel=merged_channel,
            )

        return self

    def __repr__(self) -> str:
        return f"ToolAccessPolicy({self._description})"
