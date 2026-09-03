"""Tests for uvx fallback in the provider registry.

The rule these pin changed in #1192. `uvx_package` used to be filled in for
every npm server by transforming its name (`@modelcontextprotocol/server-X` ->
`mcp-server-X`), and the tests here asserted that convention -- that every
starter server had one and that the names matched the pattern. Seven of the ten
names it produced were wrong: five distributions do not exist or ship no
executable, and two belong to other people. So the tests asserted the shape of
the bug.

What is asserted now is the rule that replaced it: a server carries a
`uvx_package` only where that PyPI distribution is the official server, which is
a fact about the world rather than a pattern, so the registry records it per
server and this file pins which ones were checked.
"""

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
        """A server with an official PyPI distribution runs on uvx alone."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("fetch")
        assert provider.uvx_package is not None
        assert provider.is_available(deps) is True

    def test_provider_unavailable_without_any_runtime(self):
        """Provider should be unavailable when no runtime available."""
        deps = make_deps(npx=False, uvx=False)
        provider = get_provider("filesystem")
        assert provider.is_available(deps) is False

    def test_provider_without_uvx_package_needs_npx(self):
        """A server with no official PyPI distribution needs npx."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("puppeteer")  # No Python equivalent
        assert provider.uvx_package is None
        assert provider.is_available(deps) is False

    def test_filesystem_needs_npx_because_its_pypi_name_does_not_exist(self):
        """`mcp-server-filesystem` is not on PyPI, so uvx alone cannot run it.

        This is the case that used to produce a configuration `init` could not
        start: uvx was preferred, the package did not resolve, and two of the
        three servers in the default bundle died on first run.
        """
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("filesystem")
        assert provider.uvx_package is None
        assert provider.is_available(deps) is False

    def test_preferred_runtime_is_uvx_when_both_available(self):
        """Should prefer uvx when both npx and uvx are available."""
        deps = make_deps(npx=True, uvx=True)
        provider = get_provider("fetch")
        assert provider.get_preferred_runtime(deps) == "uvx"

    def test_preferred_runtime_is_npx_when_only_npx_available(self):
        """Should use npx when only npx is available."""
        deps = make_deps(npx=True, uvx=False)
        provider = get_provider("fetch")
        assert provider.get_preferred_runtime(deps) == "npx"

    def test_preferred_runtime_is_uvx_when_only_uvx_available(self):
        """Should use uvx when only uvx is available."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("fetch")
        assert provider.get_preferred_runtime(deps) == "uvx"

    def test_get_command_package_returns_uvx_package(self):
        """Should return the uvx package name when uvx is preferred."""
        deps = make_deps(npx=False, uvx=True)
        provider = get_provider("fetch")
        assert provider.get_command_package(deps) == "mcp-server-fetch"

    def test_get_command_package_returns_npx_package(self):
        """Should return npx package name when npx is preferred."""
        deps = make_deps(npx=True, uvx=False)
        provider = get_provider("filesystem")
        assert provider.get_command_package(deps) == "@modelcontextprotocol/server-filesystem"

    def test_unavailable_reason_shows_both_options(self):
        """Should show both npx and uvx in unavailable reason."""
        deps = make_deps(npx=False, uvx=False)
        provider = get_provider("fetch")
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

    def test_only_the_checked_distributions_carry_a_uvx_package(self):
        """A uvx package is a claim that a distribution exists and is official.

        The two here were verified against PyPI: both are published by
        Anthropic, PBC from `modelcontextprotocol/servers`. Adding a third means
        checking `.info.author` and `.info.project_urls` for that name first --
        the registry comment says how -- and this list is what makes that a
        deliberate act rather than a pattern match.
        """
        from mcp_hangar.server.cli.services.mcp_server_registry import _PROVIDERS

        with_uvx = sorted(p.name for p in _PROVIDERS if p.uvx_package)

        assert with_uvx == ["fetch", "git"]

    def test_uvx_package_names_follow_convention(self):
        """The names that remain still follow the mcp-server-* convention."""
        for name in ["fetch", "git"]:
            provider = get_provider(name)
            assert provider.uvx_package is not None
            assert provider.uvx_package.startswith("mcp-server-")
