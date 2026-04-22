"""Bundle resolution - Resolve bundle selections to mcp_server lists.

This module handles the logic of resolving bundle selections to a flat
list of mcp_servers, handling dependencies, conflicts, and deduplication.
"""

from dataclasses import dataclass, field

from .definitions import Bundle, BUNDLES, get_bundle, get_mcp_server_definition, PROVIDERS


@dataclass
class ResolutionResult:
    """Result of bundle resolution.

    Contains the resolved list of mcp_servers and any warnings
    or conflicts that were encountered.
    """

    mcp_servers: list[str]
    """List of resolved mcp_server names in dependency order."""

    warnings: list[str] = field(default_factory=list)
    """Warnings encountered during resolution."""

    conflicts: list[tuple[str, str]] = field(default_factory=list)
    """Pairs of conflicting mcp_servers that were both requested."""

    missing_deps: list[tuple[str, str]] = field(default_factory=list)
    """Missing dependencies as (mcp_server, missing_dep) pairs."""


class BundleResolver:
    """Resolves bundle and mcp_server selections to a concrete mcp_server list.

    Handles:
    - Bundle inheritance (includes)
    - McpServer dependencies
    - McpServer conflicts
    - Deduplication
    - Explicit additions and removals
    """

    def __init__(self):
        self._bundles = BUNDLES
        self._mcp_servers = PROVIDERS

    def resolve(
        self,
        bundles: list[str] | None = None,
        mcp_servers: list[str] | None = None,
        without: list[str] | None = None,
    ) -> ResolutionResult:
        """Resolve bundle and mcp_server selections.

        Args:
            bundles: List of bundle names to include
            mcp_servers: Additional mcp_servers to add explicitly
            without: McpServers to exclude from the result

        Returns:
            ResolutionResult with resolved mcp_servers and any issues

        Example:
            resolver = BundleResolver()
            result = resolver.resolve(
                bundles=["starter", "data"],
                without=["memory"],
            )
            # result.mcp_servers = ["filesystem", "fetch", "sqlite", "postgres"]
        """
        result = ResolutionResult(mcp_servers=[])

        # Collect all mcp_servers from bundles
        bundle_mcp_servers: set[str] = set()
        if bundles:
            for bundle_name in bundles:
                bundle = get_bundle(bundle_name)
                if bundle:
                    self._expand_bundle(bundle, bundle_mcp_servers, result)
                else:
                    result.warnings.append(f"Unknown bundle: {bundle_name}")

        # Add explicit mcp_servers
        explicit_mcp_servers: set[str] = set()
        if mcp_servers:
            for name in mcp_servers:
                if name in self._mcp_servers:
                    explicit_mcp_servers.add(name)
                else:
                    result.warnings.append(f"Unknown mcp_server: {name}")

        # Combine all mcp_servers
        all_mcp_servers = bundle_mcp_servers | explicit_mcp_servers

        # Remove exclusions
        excluded: set[str] = set()
        if without:
            excluded = set(without)
            all_mcp_servers -= excluded

        # Check for conflicts
        self._check_conflicts(all_mcp_servers, result)

        # Resolve dependencies
        ordered = self._resolve_dependencies(all_mcp_servers, excluded, result)

        result.mcp_servers = ordered
        return result

    def _expand_bundle(
        self,
        bundle: Bundle,
        mcp_servers: set[str],
        result: ResolutionResult,
    ) -> None:
        """Recursively expand a bundle including its includes.

        Args:
            bundle: Bundle to expand
            mcp_servers: Set to add mcp_servers to
            result: Resolution result for warnings
        """
        # First, expand included bundles
        for included_name in bundle.includes:
            included = get_bundle(included_name)
            if included:
                self._expand_bundle(included, mcp_servers, result)
            else:
                result.warnings.append(f"Bundle '{bundle.name}' includes unknown bundle: {included_name}")

        # Then add this bundle's mcp_servers
        mcp_servers.update(bundle.mcp_servers)

    def _check_conflicts(
        self,
        mcp_servers: set[str],
        result: ResolutionResult,
    ) -> None:
        """Check for conflicting mcp_servers.

        Args:
            mcp_servers: Set of mcp_server names
            result: Resolution result to add conflicts to
        """
        for name in mcp_servers:
            definition = get_mcp_server_definition(name)
            if definition:
                for conflict in definition.conflicts:
                    if conflict in mcp_servers:
                        # Only report each conflict once
                        pair = tuple(sorted([name, conflict]))
                        if pair not in result.conflicts:
                            result.conflicts.append(pair)

    def _resolve_dependencies(
        self,
        mcp_servers: set[str],
        excluded: set[str],
        result: ResolutionResult,
    ) -> list[str]:
        """Resolve dependencies and return ordered mcp_server list.

        Uses topological sort to ensure dependencies come before
        dependents in the returned list.

        Args:
            mcp_servers: Set of mcp_server names
            excluded: McpServers that were explicitly excluded
            result: Resolution result for warnings

        Returns:
            Ordered list of mcp_servers
        """
        # Check for missing dependencies
        all_needed: set[str] = set(mcp_servers)
        for name in mcp_servers:
            definition = get_mcp_server_definition(name)
            if definition:
                for dep in definition.dependencies:
                    if dep not in mcp_servers:
                        if dep in excluded:
                            result.warnings.append(f"McpServer '{name}' depends on excluded mcp_server '{dep}'")
                            result.missing_deps.append((name, dep))
                        elif dep in self._mcp_servers:
                            # Auto-add dependency
                            all_needed.add(dep)
                        else:
                            result.warnings.append(f"McpServer '{name}' has unknown dependency: {dep}")
                            result.missing_deps.append((name, dep))

        # Simple topological sort
        # For now, we don't have complex dependency chains, so a simple
        # approach is sufficient
        ordered: list[str] = []
        remaining = set(all_needed)

        # First pass: mcp_servers with no dependencies
        for name in sorted(remaining):
            definition = get_mcp_server_definition(name)
            if not definition or not definition.dependencies:
                ordered.append(name)

        remaining -= set(ordered)

        # Second pass: mcp_servers with dependencies (sorted for determinism)
        ordered.extend(sorted(remaining))

        return ordered

    def get_bundle_mcp_servers(self, bundle_name: str) -> list[str]:
        """Get the list of mcp_servers for a bundle (including inherited).

        Args:
            bundle_name: Name of the bundle

        Returns:
            List of mcp_server names, or empty list if bundle not found
        """
        bundle = get_bundle(bundle_name)
        if not bundle:
            return []

        mcp_servers: set[str] = set()
        result = ResolutionResult(mcp_servers=[])
        self._expand_bundle(bundle, mcp_servers, result)
        return sorted(mcp_servers)


def resolve_bundles(
    bundles: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    without: list[str] | None = None,
) -> ResolutionResult:
    """Convenience function to resolve bundles.

    This is a shortcut for creating a BundleResolver and calling resolve().

    Args:
        bundles: List of bundle names
        mcp_servers: Additional mcp_servers to add
        without: McpServers to exclude

    Returns:
        ResolutionResult with resolved mcp_servers
    """
    resolver = BundleResolver()
    return resolver.resolve(bundles=bundles, mcp_servers=mcp_servers, without=without)


__all__ = [
    "ResolutionResult",
    "BundleResolver",
    "resolve_bundles",
]
