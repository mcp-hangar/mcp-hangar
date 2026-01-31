"""Provider registry - unified provider definitions for CLI commands.

Consolidates provider metadata previously duplicated across init.py and add.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDefinition:
    """Definition of an MCP provider."""

    name: str
    description: str
    package: str
    category: str
    install_type: str = "npx"
    requires_config: bool = False
    config_prompt: str | None = None
    config_type: str | None = None  # "path", "secret", "string"
    env_var: str | None = None
    official: bool = True


# All known providers with their configurations
_PROVIDERS: list[ProviderDefinition] = [
    # Starter (recommended for everyone)
    ProviderDefinition(
        name="filesystem",
        description="Read and write local files",
        package="@anthropic/mcp-server-filesystem",
        category="Starter",
        requires_config=True,
        config_prompt="Directory to allow access to",
        config_type="path",
    ),
    ProviderDefinition(
        name="fetch",
        description="Make HTTP requests to fetch web content",
        package="@anthropic/mcp-server-fetch",
        category="Starter",
    ),
    ProviderDefinition(
        name="memory",
        description="Persistent key-value storage for context",
        package="@anthropic/mcp-server-memory",
        category="Starter",
    ),
    # Developer Tools
    ProviderDefinition(
        name="github",
        description="GitHub repos, issues, PRs",
        package="@anthropic/mcp-server-github",
        category="Developer Tools",
        requires_config=True,
        config_prompt="GitHub personal access token",
        config_type="secret",
        env_var="GITHUB_TOKEN",
    ),
    ProviderDefinition(
        name="git",
        description="Local git operations",
        package="@anthropic/mcp-server-git",
        category="Developer Tools",
    ),
    # Data & Databases
    ProviderDefinition(
        name="sqlite",
        description="Query SQLite databases",
        package="@anthropic/mcp-server-sqlite",
        category="Data & Databases",
        requires_config=True,
        config_prompt="Path to SQLite database file",
        config_type="path",
    ),
    ProviderDefinition(
        name="postgres",
        description="Query PostgreSQL databases",
        package="@anthropic/mcp-server-postgres",
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
        package="@anthropic/mcp-server-slack",
        category="Integrations",
        requires_config=True,
        config_prompt="Slack bot token",
        config_type="secret",
        env_var="SLACK_BOT_TOKEN",
    ),
    ProviderDefinition(
        name="puppeteer",
        description="Browser automation",
        package="@anthropic/mcp-server-puppeteer",
        category="Integrations",
    ),
    ProviderDefinition(
        name="brave-search",
        description="Brave Search API",
        package="@anthropic/mcp-server-brave-search",
        category="Integrations",
        requires_config=True,
        config_prompt="Brave Search API key",
        config_type="secret",
        env_var="BRAVE_API_KEY",
    ),
    ProviderDefinition(
        name="google-maps",
        description="Google Maps API",
        package="@anthropic/mcp-server-google-maps",
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
