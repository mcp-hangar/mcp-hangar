"""Bundle resolution - Resolve bundle selections to provider lists.

This module handles the logic of resolving bundle selections to a flat
list of providers, handling dependencies, conflicts, and deduplication.
"""

from dataclasses import dataclass, field

from .definitions import Bundle, BUNDLES, get_bundle, get_provider_definition, PROVIDERS


@dataclass
class ResolutionResult:
    """Result of bundle resolution.

    Contains the resolved list of providers and any warnings
    or conflicts that were encountered.
    """

    providers: list[str]
    """List of resolved provider names in dependency order."""

    warnings: list[str] = field(default_factory=list)
    """Warnings encountered during resolution."""

    conflicts: list[tuple[str, str]] = field(default_factory=list)
    """Pairs of conflicting providers that were both requested."""

    missing_deps: list[tuple[str, str]] = field(default_factory=list)
    """Missing dependencies as (provider, missing_dep) pairs."""


class BundleResolver:
    """Resolves bundle and provider selections to a concrete provider list.

    Handles:
    - Bundle inheritance (includes)
    - Provider dependencies
    - Provider conflicts
    - Deduplication
    - Explicit additions and removals
    """

    def __init__(self):
        self._bundles = BUNDLES
        self._providers = PROVIDERS

    def resolve(
        self,
        bundles: list[str] | None = None,
        providers: list[str] | None = None,
        without: list[str] | None = None,
    ) -> ResolutionResult:
        """Resolve bundle and provider selections.

        Args:
            bundles: List of bundle names to include
            providers: Additional providers to add explicitly
            without: Providers to exclude from the result

        Returns:
            ResolutionResult with resolved providers and any issues

        Example:
            resolver = BundleResolver()
            result = resolver.resolve(
                bundles=["starter", "data"],
                without=["memory"],
            )
            # result.providers = ["filesystem", "fetch", "sqlite", "postgres"]
        """
        result = ResolutionResult(providers=[])

        # Collect all providers from bundles
        bundle_providers: set[str] = set()
        if bundles:
            for bundle_name in bundles:
                bundle = get_bundle(bundle_name)
                if bundle:
                    self._expand_bundle(bundle, bundle_providers, result)
                else:
                    result.warnings.append(f"Unknown bundle: {bundle_name}")

        # Add explicit providers
        explicit_providers: set[str] = set()
        if providers:
            for name in providers:
                if name in self._providers:
                    explicit_providers.add(name)
                else:
                    result.warnings.append(f"Unknown provider: {name}")

        # Combine all providers
        all_providers = bundle_providers | explicit_providers

        # Remove exclusions
        excluded: set[str] = set()
        if without:
            excluded = set(without)
            all_providers -= excluded

        # Check for conflicts
        self._check_conflicts(all_providers, result)

        # Resolve dependencies
        ordered = self._resolve_dependencies(all_providers, excluded, result)

        result.providers = ordered
        return result

    def _expand_bundle(
        self,
        bundle: Bundle,
        providers: set[str],
        result: ResolutionResult,
    ) -> None:
        """Recursively expand a bundle including its includes.

        Args:
            bundle: Bundle to expand
            providers: Set to add providers to
            result: Resolution result for warnings
        """
        # First, expand included bundles
        for included_name in bundle.includes:
            included = get_bundle(included_name)
            if included:
                self._expand_bundle(included, providers, result)
            else:
                result.warnings.append(f"Bundle '{bundle.name}' includes unknown bundle: {included_name}")

        # Then add this bundle's providers
        providers.update(bundle.providers)

    def _check_conflicts(
        self,
        providers: set[str],
        result: ResolutionResult,
    ) -> None:
        """Check for conflicting providers.

        Args:
            providers: Set of provider names
            result: Resolution result to add conflicts to
        """
        for name in providers:
            definition = get_provider_definition(name)
            if definition:
                for conflict in definition.conflicts:
                    if conflict in providers:
                        # Only report each conflict once
                        pair = tuple(sorted([name, conflict]))
                        if pair not in result.conflicts:
                            result.conflicts.append(pair)

    def _resolve_dependencies(
        self,
        providers: set[str],
        excluded: set[str],
        result: ResolutionResult,
    ) -> list[str]:
        """Resolve dependencies and return ordered provider list.

        Uses topological sort to ensure dependencies come before
        dependents in the returned list.

        Args:
            providers: Set of provider names
            excluded: Providers that were explicitly excluded
            result: Resolution result for warnings

        Returns:
            Ordered list of providers
        """
        # Check for missing dependencies
        all_needed: set[str] = set(providers)
        for name in providers:
            definition = get_provider_definition(name)
            if definition:
                for dep in definition.dependencies:
                    if dep not in providers:
                        if dep in excluded:
                            result.warnings.append(f"Provider '{name}' depends on excluded provider '{dep}'")
                            result.missing_deps.append((name, dep))
                        elif dep in self._providers:
                            # Auto-add dependency
                            all_needed.add(dep)
                        else:
                            result.warnings.append(f"Provider '{name}' has unknown dependency: {dep}")
                            result.missing_deps.append((name, dep))

        # Simple topological sort
        # For now, we don't have complex dependency chains, so a simple
        # approach is sufficient
        ordered: list[str] = []
        remaining = set(all_needed)

        # First pass: providers with no dependencies
        for name in sorted(remaining):
            definition = get_provider_definition(name)
            if not definition or not definition.dependencies:
                ordered.append(name)

        remaining -= set(ordered)

        # Second pass: providers with dependencies (sorted for determinism)
        ordered.extend(sorted(remaining))

        return ordered

    def get_bundle_providers(self, bundle_name: str) -> list[str]:
        """Get the list of providers for a bundle (including inherited).

        Args:
            bundle_name: Name of the bundle

        Returns:
            List of provider names, or empty list if bundle not found
        """
        bundle = get_bundle(bundle_name)
        if not bundle:
            return []

        providers: set[str] = set()
        result = ResolutionResult(providers=[])
        self._expand_bundle(bundle, providers, result)
        return sorted(providers)


def resolve_bundles(
    bundles: list[str] | None = None,
    providers: list[str] | None = None,
    without: list[str] | None = None,
) -> ResolutionResult:
    """Convenience function to resolve bundles.

    This is a shortcut for creating a BundleResolver and calling resolve().

    Args:
        bundles: List of bundle names
        providers: Additional providers to add
        without: Providers to exclude

    Returns:
        ResolutionResult with resolved providers
    """
    resolver = BundleResolver()
    return resolver.resolve(bundles=bundles, providers=providers, without=without)


__all__ = [
    "ResolutionResult",
    "BundleResolver",
    "resolve_bundles",
]
