"""Tests for uvx fallback in provider registry."""

import pytest

from mcp_hangar.server.cli.services.dependency_detector import clear_cache, DependencyStatus, RuntimeInfo
from mcp_hangar.server.cli.services.provider_registry import get_provider


@pytest.fixture(autouse=True)
def clear_dep_cache():
    """Clear dependency cache before each test."""
    clear_cache()
    yield
    clear_cache()


def make_deps(npx: bool = False, uvx: bool = False) -> DependencyStatus:
    """Helper to create DependencyStatus with specified runtimes."""
    return DependencyStatus(
        npx=RuntimeInfo("npx", "/usr/bin/npx" if npx else None, npx),
        uvx=RuntimeInfo("uvx", "/usr/bin/uvx" if uvx else None, uvx),
        docker=RuntimeInfo("docker", None, False),
        podman=RuntimeInfo("podman", None, False),
    )


class TestProviderUvxFallback:
    """Tests for uvx fallback when npx not available."""

    def test_provider_available_with_npx(self):
        """Provider should be available when npx is available."""
        deps = make_deps(npx=True, uvx=False)
        provider = get_provider("filesystem")
        assert provider.is_available(deps) is True

    def test_provider_available_with_uvx_only(self):
        """Provider should be available when only uvx is available and has uvx_package."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("filesystem")
        assert provider.uvx_package is not None
        assert provider.is_available(deps) is True

    def test_provider_unavailable_without_any_runtime(self):
        """Provider should be unavailable when no runtime available."""
        deps = make_deps(npx=False, uvx=False)
        provider = get_provider("filesystem")
        assert provider.is_available(deps) is False

    def test_provider_without_uvx_package_needs_npx(self):
        """Provider without uvx_package should need npx."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("puppeteer")  # No Python equivalent
        assert provider.uvx_package is None
        assert provider.is_available(deps) is False

    def test_preferred_runtime_is_uvx_when_both_available(self):
        """Should prefer uvx when both npx and uvx are available."""
        deps = make_deps(npx=True, uvx=True)
        provider = get_provider("filesystem")
        assert provider.get_preferred_runtime(deps) == "uvx"

    def test_preferred_runtime_is_npx_when_only_npx_available(self):
        """Should use npx when only npx is available."""
        deps = make_deps(npx=True, uvx=False)
        provider = get_provider("filesystem")
        assert provider.get_preferred_runtime(deps) == "npx"

    def test_preferred_runtime_is_uvx_when_only_uvx_available(self):
        """Should use uvx when only uvx is available."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("filesystem")
        assert provider.get_preferred_runtime(deps) == "uvx"

    def test_get_command_package_returns_uvx_package(self):
        """Should return uvx package name when uvx is preferred."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("filesystem")
        assert provider.get_command_package(deps) == "mcp-server-filesystem"

    def test_get_command_package_returns_npx_package(self):
        """Should return npx package name when npx is preferred."""
        deps = make_deps(npx=True, uvx=False)
        provider = get_provider("filesystem")
        assert provider.get_command_package(deps) == "@modelcontextprotocol/server-filesystem"

    def test_unavailable_reason_shows_both_options(self):
        """Should show both npx and uvx in unavailable reason."""
        deps = make_deps(npx=False, uvx=False)
        provider = get_provider("filesystem")
        reason = provider.get_unavailable_reason(deps)
        assert "npx" in reason
        assert "uvx" in reason

    def test_unavailable_reason_for_npx_only_provider(self):
        """Provider without uvx_package should only show npx in reason."""
        deps = make_deps(npx=False, uvx=False)
        provider = get_provider("puppeteer")
        reason = provider.get_unavailable_reason(deps)
        assert "npx" in reason
        # Should not mention uvx since there's no uvx alternative
        assert reason == "requires npx"


class TestProviderDefinitionUvx:
    """Tests for ProviderDefinition with uvx support."""

    def test_all_starter_providers_have_uvx_package(self):
        """All starter providers should have uvx package for Python fallback."""
        starter_providers = ["filesystem", "fetch", "memory"]
        for name in starter_providers:
            provider = get_provider(name)
            assert provider.uvx_package is not None, f"{name} should have uvx_package"

    def test_uvx_package_names_follow_convention(self):
        """uvx package names should follow mcp-server-* convention."""
        for name in ["filesystem", "fetch", "memory", "github", "git"]:
            provider = get_provider(name)
            if provider.uvx_package:
                assert provider.uvx_package.startswith(
                    "mcp-server-"
                ), f"{name} uvx_package should start with mcp-server-"
