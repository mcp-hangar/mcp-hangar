"""Bundle and provider definitions.

This module defines the available provider bundles and their configurations.
These are pure domain objects with no external dependencies.
"""

from dataclasses import dataclass, field
from enum import Enum


class InstallType(Enum):
    """How a provider is installed/executed."""

    NPX = "npx"  # Node.js via npx
    UVX = "uvx"  # Python via uvx
    DOCKER = "docker"  # Docker container
    BINARY = "binary"  # Pre-built binary


class ConfigType(Enum):
    """Type of configuration required."""

    NONE = "none"  # No configuration needed
    PATH = "path"  # File or directory path
    SECRET = "secret"  # Sensitive value (API key, token)
    STRING = "string"  # Plain string value
    URL = "url"  # URL value


@dataclass(frozen=True)
class ProviderDefinition:
    """Definition of an MCP provider.

    This is a domain value object that describes a provider's
    metadata and configuration requirements.
    """

    name: str
    """Unique provider identifier."""

    description: str
    """Human-readable description."""

    package: str
    """Package name (npm package, PyPI package, or Docker image)."""

    install_type: InstallType = InstallType.NPX
    """How to install/execute the provider."""

    requires_config: bool = False
    """Whether configuration is required before use."""

    config_type: ConfigType = ConfigType.NONE
    """Type of configuration if required."""

    config_prompt: str = ""
    """Prompt to show when collecting configuration."""

    env_var: str | None = None
    """Environment variable name for secrets."""

    dependencies: list[str] = field(default_factory=list)
    """Other providers this one depends on."""

    conflicts: list[str] = field(default_factory=list)
    """Providers that conflict with this one."""

    official: bool = True
    """Whether this is an official Anthropic provider."""

    category: str = ""
    """Category for grouping (e.g., 'filesystem', 'api', 'database')."""


@dataclass(frozen=True)
class Bundle:
    """A curated collection of providers for a use case.

    Bundles help users quickly set up a useful configuration
    without having to choose individual providers.
    """

    name: str
    """Bundle identifier (e.g., 'starter', 'developer')."""

    display_name: str
    """Human-readable name."""

    description: str
    """Description of what this bundle is for."""

    providers: list[str]
    """List of provider names included in this bundle."""

    includes: list[str] = field(default_factory=list)
    """Other bundles this one includes (inheritance)."""


# Provider definitions
PROVIDERS: dict[str, ProviderDefinition] = {
    # Starter providers
    "filesystem": ProviderDefinition(
        name="filesystem",
        description="Read and write local files",
        package="@anthropic/mcp-server-filesystem",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.PATH,
        config_prompt="Directory to allow access to",
        category="filesystem",
    ),
    "fetch": ProviderDefinition(
        name="fetch",
        description="Make HTTP requests to fetch web content",
        package="@anthropic/mcp-server-fetch",
        install_type=InstallType.NPX,
        requires_config=False,
        category="network",
    ),
    "memory": ProviderDefinition(
        name="memory",
        description="Persistent key-value storage for context",
        package="@anthropic/mcp-server-memory",
        install_type=InstallType.NPX,
        requires_config=False,
        category="storage",
    ),
    # Developer providers
    "github": ProviderDefinition(
        name="github",
        description="GitHub repos, issues, PRs",
        package="@anthropic/mcp-server-github",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.SECRET,
        config_prompt="GitHub personal access token",
        env_var="GITHUB_TOKEN",
        category="vcs",
    ),
    "git": ProviderDefinition(
        name="git",
        description="Local git operations",
        package="@anthropic/mcp-server-git",
        install_type=InstallType.NPX,
        requires_config=False,
        category="vcs",
    ),
    "gitlab": ProviderDefinition(
        name="gitlab",
        description="GitLab repos, issues, MRs",
        package="@anthropic/mcp-server-gitlab",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.SECRET,
        config_prompt="GitLab personal access token",
        env_var="GITLAB_TOKEN",
        conflicts=["github"],  # Typically use one or the other
        category="vcs",
    ),
    # Data providers
    "sqlite": ProviderDefinition(
        name="sqlite",
        description="Query SQLite databases",
        package="@anthropic/mcp-server-sqlite",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.PATH,
        config_prompt="Path to SQLite database file",
        category="database",
    ),
    "postgres": ProviderDefinition(
        name="postgres",
        description="Query PostgreSQL databases",
        package="@anthropic/mcp-server-postgres",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.SECRET,
        config_prompt="PostgreSQL connection string",
        env_var="DATABASE_URL",
        category="database",
    ),
    # Communication providers
    "slack": ProviderDefinition(
        name="slack",
        description="Slack workspace integration",
        package="@anthropic/mcp-server-slack",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.SECRET,
        config_prompt="Slack bot token",
        env_var="SLACK_BOT_TOKEN",
        category="communication",
    ),
    # Browser/automation providers
    "puppeteer": ProviderDefinition(
        name="puppeteer",
        description="Browser automation with Puppeteer",
        package="@anthropic/mcp-server-puppeteer",
        install_type=InstallType.NPX,
        requires_config=False,
        category="automation",
    ),
    # Search providers
    "brave-search": ProviderDefinition(
        name="brave-search",
        description="Web search via Brave Search API",
        package="@anthropic/mcp-server-brave-search",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.SECRET,
        config_prompt="Brave Search API key",
        env_var="BRAVE_API_KEY",
        category="search",
    ),
    "google-maps": ProviderDefinition(
        name="google-maps",
        description="Google Maps and Places API",
        package="@anthropic/mcp-server-google-maps",
        install_type=InstallType.NPX,
        requires_config=True,
        config_type=ConfigType.SECRET,
        config_prompt="Google Maps API key",
        env_var="GOOGLE_MAPS_API_KEY",
        category="maps",
    ),
    # Time provider
    "time": ProviderDefinition(
        name="time",
        description="Current time and timezone operations",
        package="@anthropic/mcp-server-time",
        install_type=InstallType.NPX,
        requires_config=False,
        category="utility",
    ),
    # Sequential thinking provider
    "sequential-thinking": ProviderDefinition(
        name="sequential-thinking",
        description="Step-by-step reasoning tool",
        package="@anthropic/mcp-server-sequential-thinking",
        install_type=InstallType.NPX,
        requires_config=False,
        category="reasoning",
    ),
}


# Bundle definitions
STARTER_BUNDLE = Bundle(
    name="starter",
    display_name="Starter",
    description="Essential providers for general use - files, web requests, and memory",
    providers=["filesystem", "fetch", "memory"],
)

DEVELOPER_BUNDLE = Bundle(
    name="developer",
    display_name="Developer",
    description="Tools for software development - includes Starter plus GitHub and Git",
    providers=["github", "git"],
    includes=["starter"],
)

DATA_BUNDLE = Bundle(
    name="data",
    display_name="Data & Analytics",
    description="Database access for data analysis - includes Starter plus SQLite and PostgreSQL",
    providers=["sqlite", "postgres"],
    includes=["starter"],
)

BUNDLES: dict[str, Bundle] = {
    "starter": STARTER_BUNDLE,
    "developer": DEVELOPER_BUNDLE,
    "data": DATA_BUNDLE,
}


def get_bundle(name: str) -> Bundle | None:
    """Get a bundle by name.

    Args:
        name: Bundle name (case-insensitive)

    Returns:
        Bundle object or None if not found
    """
    return BUNDLES.get(name.lower())


def get_all_bundles() -> list[Bundle]:
    """Get all available bundles.

    Returns:
        List of all bundle definitions
    """
    return list(BUNDLES.values())


def get_provider_definition(name: str) -> ProviderDefinition | None:
    """Get a provider definition by name.

    Args:
        name: Provider name

    Returns:
        ProviderDefinition or None if not found
    """
    return PROVIDERS.get(name)


__all__ = [
    "InstallType",
    "ConfigType",
    "ProviderDefinition",
    "Bundle",
    "PROVIDERS",
    "BUNDLES",
    "STARTER_BUNDLE",
    "DEVELOPER_BUNDLE",
    "DATA_BUNDLE",
    "get_bundle",
    "get_all_bundles",
    "get_provider_definition",
]
