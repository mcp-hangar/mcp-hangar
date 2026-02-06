"""Provider registry - unified provider definitions for CLI commands.

Consolidates provider metadata previously duplicated across init.py and add.py.
"""

from dataclasses import dataclass

from .dependency_detector import DependencyStatus, detect_dependencies, is_provider_available


@dataclass(frozen=True)
class ProviderDefinition:
    """Definition of an MCP provider."""

    name: str
    description: str
    package: str  # npx package name (e.g., @anthropic/mcp-server-fetch)
    category: str
    install_type: str = "npx"  # primary: npx, uvx, docker, binary
    uvx_package: str | None = None  # uvx package name if available (e.g., mcp-server-fetch)
    requires_config: bool = False
    config_prompt: str | None = None
    config_type: str | None = None  # "path", "secret", "string"
    env_var: str | None = None
    official: bool = True

    def is_available(self, deps: DependencyStatus | None = None) -> bool:
        """Check if this provider can be installed with current dependencies.

        A provider is available if:
        - Primary install_type runtime is available, OR
        - uvx_package exists and uvx is available
        """
        if deps is None:
            deps = detect_dependencies()

        # Check primary runtime
        if is_provider_available(self.install_type, deps):
            return True

        # Check uvx fallback for npx providers
        if self.install_type == "npx" and self.uvx_package and deps.uvx.available:
            return True

        return False

    def get_unavailable_reason(self, deps: DependencyStatus | None = None) -> str | None:
        """Get reason why provider is unavailable, or None if available."""
        if self.is_available(deps):
            return None

        if self.uvx_package:
            return f"requires {self.install_type} or uvx"
        return f"requires {self.install_type}"

    def get_preferred_runtime(self, deps: DependencyStatus | None = None) -> str:
        """Get the preferred runtime based on available dependencies.

        Priority: uvx > npx (dogfooding - our tool is Python-based)

        Returns:
            "uvx" or "npx" or the primary install_type
        """
        if deps is None:
            deps = detect_dependencies()

        # For npx providers with uvx alternative, prefer uvx
        if self.install_type == "npx" and self.uvx_package:
            if deps.uvx.available:
                return "uvx"
            if deps.npx.available:
                return "npx"

        # Default to primary install type
        return self.install_type

    def get_command_package(self, deps: DependencyStatus | None = None) -> str:
        """Get the package name for the preferred runtime.

        Returns:
            Package name appropriate for the runtime (uvx_package or package)
        """
        runtime = self.get_preferred_runtime(deps)
        if runtime == "uvx" and self.uvx_package:
            return self.uvx_package
        return self.package


# All known providers with their configurations
# uvx_package maps to PyPI packages that provide equivalent functionality
_PROVIDERS: list[ProviderDefinition] = [
    # Starter (recommended for everyone)
    ProviderDefinition(
        name="filesystem",
        description="Read and write local files",
        package="@modelcontextprotocol/server-filesystem",
        uvx_package="mcp-server-filesystem",
        category="Starter",
        requires_config=True,
        config_prompt="Directory to allow access to",
        config_type="path",
    ),
    ProviderDefinition(
        name="fetch",
        description="Make HTTP requests to fetch web content",
        package="@modelcontextprotocol/server-fetch",
        uvx_package="mcp-server-fetch",
        category="Starter",
    ),
    ProviderDefinition(
        name="memory",
        description="Persistent key-value storage for context",
        package="@modelcontextprotocol/server-memory",
        uvx_package="mcp-server-memory",
        category="Starter",
    ),
    # Developer Tools
    ProviderDefinition(
        name="github",
        description="GitHub repos, issues, PRs",
        package="@modelcontextprotocol/server-github",
        uvx_package="mcp-server-github",
        category="Developer Tools",
        requires_config=True,
        config_prompt="GitHub personal access token",
        config_type="secret",
        env_var="GITHUB_TOKEN",
    ),
    ProviderDefinition(
        name="git",
        description="Local git operations",
        package="@modelcontextprotocol/server-git",
        uvx_package="mcp-server-git",
        category="Developer Tools",
    ),
    # Data & Databases
    ProviderDefinition(
        name="sqlite",
        description="Query SQLite databases",
        package="@modelcontextprotocol/server-sqlite",
        uvx_package="mcp-server-sqlite",
        category="Data & Databases",
        requires_config=True,
        config_prompt="Path to SQLite database file",
        config_type="path",
    ),
    ProviderDefinition(
        name="postgres",
        description="Query PostgreSQL databases",
        package="@modelcontextprotocol/server-postgres",
        uvx_package="mcp-server-postgres",
        category="Data & Databases",
        requires_config=True,
        config_prompt="PostgreSQL connection string",
        config_type="secret",
        env_var="DATABASE_URL",
    ),
    # Integrations
    ProviderDefinition(
        name="slack",
        description="Slack workspace integration",
        package="@modelcontextprotocol/server-slack",
        uvx_package="mcp-server-slack",
        category="Integrations",
        requires_config=True,
        config_prompt="Slack bot token",
        config_type="secret",
        env_var="SLACK_BOT_TOKEN",
    ),
    ProviderDefinition(
        name="puppeteer",
        description="Browser automation",
        package="@modelcontextprotocol/server-puppeteer",
        uvx_package=None,  # No Python equivalent - requires Node.js
        category="Integrations",
    ),
    ProviderDefinition(
        name="brave-search",
        description="Brave Search API",
        package="@modelcontextprotocol/server-brave-search",
        uvx_package="mcp-server-brave-search",
        category="Integrations",
        requires_config=True,
        config_prompt="Brave Search API key",
        config_type="secret",
        env_var="BRAVE_API_KEY",
    ),
    ProviderDefinition(
        name="google-maps",
        description="Google Maps API",
        package="@modelcontextprotocol/server-google-maps",
        uvx_package="mcp-server-google-maps",
        category="Integrations",
        requires_config=True,
        config_prompt="Google Maps API key",
        config_type="secret",
        env_var="GOOGLE_MAPS_API_KEY",
    ),
]

# Provider bundles for quick setup
PROVIDER_BUNDLES: dict[str, list[str]] = {
    "starter": ["filesystem", "fetch", "memory"],
    "developer": ["filesystem", "fetch", "memory", "github", "git"],
    "data": ["filesystem", "fetch", "memory", "sqlite", "postgres"],
}

# Build lookup dict for fast access
_PROVIDERS_BY_NAME: dict[str, ProviderDefinition] = {p.name: p for p in _PROVIDERS}


def get_all_providers() -> list[ProviderDefinition]:
    """Get all known providers."""
    return list(_PROVIDERS)


def get_provider(name: str) -> ProviderDefinition | None:
    """Get a provider by name."""
    return _PROVIDERS_BY_NAME.get(name)


def get_providers_by_category() -> dict[str, list[ProviderDefinition]]:
    """Get providers grouped by category."""
    result: dict[str, list[ProviderDefinition]] = {}
    for provider in _PROVIDERS:
        if provider.category not in result:
            result[provider.category] = []
        result[provider.category].append(provider)
    return result


def search_providers(query: str) -> list[ProviderDefinition]:
    """Search providers by name or description.

    Args:
        query: Search query string

    Returns:
        List of matching providers
    """
    query_lower = query.lower()
    return [p for p in _PROVIDERS if query_lower in p.name.lower() or query_lower in p.description.lower()]


def get_available_providers(deps: DependencyStatus | None = None) -> list[ProviderDefinition]:
    """Get providers that can be installed with current dependencies.

    Args:
        deps: Optional pre-detected dependencies

    Returns:
        List of available providers
    """
    if deps is None:
        deps = detect_dependencies()
    return [p for p in _PROVIDERS if p.is_available(deps)]


def get_unavailable_providers(deps: DependencyStatus | None = None) -> list[ProviderDefinition]:
    """Get providers that cannot be installed due to missing dependencies.

    Args:
        deps: Optional pre-detected dependencies

    Returns:
        List of unavailable providers
    """
    if deps is None:
        deps = detect_dependencies()
    return [p for p in _PROVIDERS if not p.is_available(deps)]


def get_providers_by_category_filtered(
    deps: DependencyStatus | None = None,
) -> tuple[dict[str, list[ProviderDefinition]], dict[str, list[ProviderDefinition]]]:
    """Get providers grouped by category, split into available and unavailable.

    Args:
        deps: Optional pre-detected dependencies

    Returns:
        Tuple of (available_by_category, unavailable_by_category)
    """
    if deps is None:
        deps = detect_dependencies()

    available: dict[str, list[ProviderDefinition]] = {}
    unavailable: dict[str, list[ProviderDefinition]] = {}

    for provider in _PROVIDERS:
        category = provider.category
        if provider.is_available(deps):
            if category not in available:
                available[category] = []
            available[category].append(provider)
        else:
            if category not in unavailable:
                unavailable[category] = []
            unavailable[category].append(provider)

    return available, unavailable


def filter_bundle_by_availability(
    bundle_name: str,
    deps: DependencyStatus | None = None,
) -> tuple[list[str], list[str]]:
    """Filter a bundle to only include available providers.

    Args:
        bundle_name: Name of the bundle
        deps: Optional pre-detected dependencies

    Returns:
        Tuple of (available_providers, unavailable_providers)
    """
    if bundle_name not in PROVIDER_BUNDLES:
        return [], []

    if deps is None:
        deps = detect_dependencies()

    bundle_providers = PROVIDER_BUNDLES[bundle_name]
    available = []
    unavailable = []

    for name in bundle_providers:
        provider = get_provider(name)
        if provider and provider.is_available(deps):
            available.append(name)
        else:
            unavailable.append(name)

    return available, unavailable
