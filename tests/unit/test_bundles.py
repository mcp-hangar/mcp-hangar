"""Tests for bundle definitions and resolution.

Tests cover bundle resolution, dependency handling, conflict detection,
and provider list generation.
"""

import pytest

from mcp_hangar.domain.bundles.definitions import (
    Bundle,
    BUNDLES,
    ConfigType,
    get_all_bundles,
    get_bundle,
    get_provider_definition,
    InstallType,
    ProviderDefinition,
    PROVIDERS,
)
from mcp_hangar.domain.bundles.resolver import BundleResolver, ResolutionResult, resolve_bundles


class TestProviderDefinition:
    """Tests for ProviderDefinition value object."""

    def test_provider_definition_is_frozen(self):
        """ProviderDefinition should be immutable."""
        definition = ProviderDefinition(
            name="test",
            description="Test provider",
            package="@test/package",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            definition.name = "changed"

    def test_provider_definition_defaults(self):
        """ProviderDefinition should have sensible defaults."""
        definition = ProviderDefinition(
            name="test",
            description="Test",
            package="test-package",
        )

        assert definition.install_type == InstallType.NPX
        assert definition.requires_config is False
        assert definition.config_type == ConfigType.NONE
        assert definition.dependencies == []
        assert definition.conflicts == []
        assert definition.official is True

    def test_provider_definition_with_config(self):
        """ProviderDefinition with required configuration."""
        definition = ProviderDefinition(
            name="github",
            description="GitHub provider",
            package="@anthropic/mcp-server-github",
            requires_config=True,
            config_type=ConfigType.SECRET,
            config_prompt="GitHub token",
            env_var="GITHUB_TOKEN",
        )

        assert definition.requires_config is True
        assert definition.config_type == ConfigType.SECRET
        assert definition.env_var == "GITHUB_TOKEN"


class TestBundle:
    """Tests for Bundle value object."""

    def test_bundle_is_frozen(self):
        """Bundle should be immutable."""
        bundle = Bundle(
            name="test",
            display_name="Test",
            description="Test bundle",
            providers=["a", "b"],
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            bundle.name = "changed"

    def test_bundle_with_includes(self):
        """Bundle can include other bundles."""
        bundle = Bundle(
            name="advanced",
            display_name="Advanced",
            description="Advanced bundle",
            providers=["extra"],
            includes=["starter"],
        )

        assert bundle.includes == ["starter"]


class TestDefinitionsRegistry:
    """Tests for the provider and bundle registries."""

    def test_starter_providers_exist(self):
        """Starter bundle providers should be in registry."""
        assert "filesystem" in PROVIDERS
        assert "fetch" in PROVIDERS
        assert "memory" in PROVIDERS

    def test_developer_providers_exist(self):
        """Developer bundle providers should be in registry."""
        assert "github" in PROVIDERS
        assert "git" in PROVIDERS

    def test_data_providers_exist(self):
        """Data bundle providers should be in registry."""
        assert "sqlite" in PROVIDERS
        assert "postgres" in PROVIDERS

    def test_bundles_exist(self):
        """All standard bundles should exist."""
        assert "starter" in BUNDLES
        assert "developer" in BUNDLES
        assert "data" in BUNDLES

    def test_get_bundle_case_insensitive(self):
        """get_bundle should be case insensitive."""
        assert get_bundle("starter") is not None
        assert get_bundle("STARTER") is not None
        assert get_bundle("Starter") is not None

    def test_get_bundle_unknown(self):
        """get_bundle returns None for unknown bundles."""
        assert get_bundle("unknown") is None

    def test_get_all_bundles(self):
        """get_all_bundles returns all bundles."""
        bundles = get_all_bundles()
        assert len(bundles) >= 3
        names = [b.name for b in bundles]
        assert "starter" in names
        assert "developer" in names
        assert "data" in names

    def test_get_provider_definition(self):
        """get_provider_definition returns correct definition."""
        definition = get_provider_definition("filesystem")
        assert definition is not None
        assert definition.name == "filesystem"
        assert definition.package == "@anthropic/mcp-server-filesystem"

    def test_get_provider_definition_unknown(self):
        """get_provider_definition returns None for unknown providers."""
        assert get_provider_definition("unknown") is None


class TestBundleResolver:
    """Tests for BundleResolver."""

    def test_resolve_single_bundle(self):
        """Resolving a single bundle returns its providers."""
        resolver = BundleResolver()
        result = resolver.resolve(bundles=["starter"])

        assert "filesystem" in result.providers
        assert "fetch" in result.providers
        assert "memory" in result.providers
        assert len(result.warnings) == 0

    def test_resolve_bundle_with_includes(self):
        """Resolving bundle with includes gets inherited providers."""
        resolver = BundleResolver()
        result = resolver.resolve(bundles=["developer"])

        # Should have starter providers
        assert "filesystem" in result.providers
        assert "fetch" in result.providers
        assert "memory" in result.providers
        # Plus developer providers
        assert "github" in result.providers
        assert "git" in result.providers

    def test_resolve_multiple_bundles_deduplicates(self):
        """Resolving multiple bundles deduplicates providers."""
        resolver = BundleResolver()
        result = resolver.resolve(bundles=["starter", "developer"])

        # Should not have duplicates
        assert len(result.providers) == len(set(result.providers))

    def test_resolve_with_explicit_providers(self):
        """Can add explicit providers to bundle."""
        resolver = BundleResolver()
        result = resolver.resolve(bundles=["starter"], providers=["sqlite"])

        assert "sqlite" in result.providers
        assert "filesystem" in result.providers

    def test_resolve_with_without_exclusions(self):
        """Can exclude providers from bundles."""
        resolver = BundleResolver()
        result = resolver.resolve(bundles=["starter"], without=["memory"])

        assert "filesystem" in result.providers
        assert "fetch" in result.providers
        assert "memory" not in result.providers

    def test_resolve_unknown_bundle_warning(self):
        """Resolving unknown bundle adds warning."""
        resolver = BundleResolver()
        result = resolver.resolve(bundles=["unknown"])

        assert len(result.warnings) > 0
        assert any("unknown" in w.lower() for w in result.warnings)

    def test_resolve_unknown_provider_warning(self):
        """Resolving unknown provider adds warning."""
        resolver = BundleResolver()
        result = resolver.resolve(providers=["nonexistent"])

        assert len(result.warnings) > 0
        assert any("nonexistent" in w.lower() for w in result.warnings)

    def test_resolve_empty_returns_empty(self):
        """Resolving with no bundles or providers returns empty."""
        resolver = BundleResolver()
        result = resolver.resolve()

        assert len(result.providers) == 0

    def test_get_bundle_providers(self):
        """get_bundle_providers returns expanded provider list."""
        resolver = BundleResolver()
        providers = resolver.get_bundle_providers("developer")

        assert "filesystem" in providers  # From starter
        assert "github" in providers  # Direct


class TestResolveBundlesConvenience:
    """Tests for the resolve_bundles convenience function."""

    def test_resolve_bundles_basic(self):
        """resolve_bundles works as shortcut."""
        result = resolve_bundles(bundles=["starter"])

        assert isinstance(result, ResolutionResult)
        assert "filesystem" in result.providers

    def test_resolve_bundles_combined(self):
        """resolve_bundles handles all arguments."""
        result = resolve_bundles(
            bundles=["starter"],
            providers=["sqlite"],
            without=["memory"],
        )

        assert "filesystem" in result.providers
        assert "sqlite" in result.providers
        assert "memory" not in result.providers
