"""Tests for the package resolver."""

from mcp_hangar.application.services.package_resolver import PackageResolver, RuntimeAvailability
from mcp_hangar.domain.contracts.registry import PackageInfo, TransportInfo


def make_package(registry_type: str, identifier: str = "test-package") -> PackageInfo:
    """Helper to create a PackageInfo for testing."""
    return PackageInfo(
        registry_type=registry_type,
        identifier=identifier,
        version="1.0.0",
        transport=TransportInfo(type="stdio"),
    )


class TestRuntimeAvailability:
    """Tests for RuntimeAvailability."""

    def test_default_values(self):
        """Test default availability values."""
        availability = RuntimeAvailability()
        assert availability.pypi is False
        assert availability.npm is False
        assert availability.oci is False
        assert availability.binary is True


class TestPackageResolver:
    """Tests for PackageResolver."""

    def test_resolve_empty_packages(self):
        """Test resolving with no packages returns None."""
        resolver = PackageResolver()
        assert resolver.resolve([]) is None

    def test_resolve_single_available_package(self):
        """Test resolving with single available package."""
        availability = RuntimeAvailability(pypi=True)
        resolver = PackageResolver(availability)
        packages = [make_package("pypi")]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "pypi"

    def test_resolve_prefers_pypi_over_npm(self):
        """Test pypi is preferred over npm."""
        availability = RuntimeAvailability(pypi=True, npm=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("npm", "npm-package"),
            make_package("pypi", "pypi-package"),
        ]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "pypi"

    def test_resolve_prefers_npm_over_oci(self):
        """Test npm is preferred over oci."""
        availability = RuntimeAvailability(npm=True, oci=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("oci", "oci-package"),
            make_package("npm", "npm-package"),
        ]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "npm"

    def test_resolve_prefers_oci_over_binary(self):
        """Test oci is preferred over binary."""
        availability = RuntimeAvailability(oci=True, binary=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("mcpb", "binary-package"),
            make_package("oci", "oci-package"),
        ]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "oci"

    def test_resolve_respects_preference(self):
        """Test user preference overrides default priority."""
        availability = RuntimeAvailability(pypi=True, npm=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("pypi", "pypi-package"),
            make_package("npm", "npm-package"),
        ]

        result = resolver.resolve(packages, preference="npm")

        assert result is not None
        assert result.registry_type == "npm"

    def test_resolve_falls_back_when_preference_unavailable(self):
        """Test falls back to priority when preference not available."""
        availability = RuntimeAvailability(pypi=True, npm=False)
        resolver = PackageResolver(availability)
        packages = [
            make_package("pypi", "pypi-package"),
            make_package("npm", "npm-package"),
        ]

        result = resolver.resolve(packages, preference="npm")

        assert result is not None
        assert result.registry_type == "pypi"

    def test_resolve_filters_unavailable_runtimes(self):
        """Test unavailable runtimes are filtered out."""
        availability = RuntimeAvailability(pypi=False, npm=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("pypi", "pypi-package"),
            make_package("npm", "npm-package"),
        ]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "npm"

    def test_resolve_returns_none_when_no_runtime_available(self):
        """Test returns None when no runtime is available."""
        availability = RuntimeAvailability(pypi=False, npm=False, oci=False, binary=False)
        resolver = PackageResolver(availability)
        packages = [
            make_package("pypi"),
            make_package("npm"),
        ]

        result = resolver.resolve(packages)

        assert result is None

    def test_get_available_runtimes(self):
        """Test getting list of available runtimes."""
        availability = RuntimeAvailability(pypi=True, npm=True, oci=False, binary=True)
        resolver = PackageResolver(availability)

        runtimes = resolver.get_available_runtimes()

        assert "pypi" in runtimes
        assert "npm" in runtimes
        assert "oci" not in runtimes
        assert "mcpb" in runtimes

    def test_resolve_all_runtimes_available(self):
        """Test resolution with all runtimes available."""
        availability = RuntimeAvailability(pypi=True, npm=True, oci=True, binary=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("mcpb"),
            make_package("oci"),
            make_package("npm"),
            make_package("pypi"),
        ]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "pypi"

    def test_resolve_unknown_registry_type(self):
        """Test unknown registry types are filtered out."""
        availability = RuntimeAvailability(pypi=True)
        resolver = PackageResolver(availability)
        packages = [
            make_package("unknown", "unknown-package"),
            make_package("pypi", "pypi-package"),
        ]

        result = resolver.resolve(packages)

        assert result is not None
        assert result.registry_type == "pypi"
