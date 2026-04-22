"""McpServer registry - unified mcp_server definitions for CLI commands.

Consolidates mcp_server metadata previously duplicated across init.py and add.py.
"""

from dataclasses import dataclass

from .dependency_detector import DependencyStatus, detect_dependencies, is_mcp_server_available


@dataclass(frozen=True)
class McpServerDefinition:
    """Definition of an MCP mcp_server."""

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
        """Check if this mcp_server can be installed with current dependencies.

        A mcp_server is available if:
        - Primary install_type runtime is available, OR
        - uvx_package exists and uvx is available
        """
        if deps is None:
            deps = detect_dependencies()

        # Check primary runtime
        if is_mcp_server_available(self.install_type, deps):
            return True

        # Check uvx fallback for npx mcp_servers
        if self.install_type == "npx" and self.uvx_package and deps.uvx.available:
            return True

        return False

    def get_unavailable_reason(self, deps: DependencyStatus | None = None) -> str | None:
        """Get reason why mcp_server is unavailable, or None if available."""
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

        # For npx mcp_servers with uvx alternative, prefer uvx
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


# All known mcp_servers with their configurations
# uvx_package maps to PyPI packages that provide equivalent functionality
_PROVIDERS: list[McpServerDefinition] = [
    # Starter (recommended for everyone)
    McpServerDefinition(
        name="filesystem",
        description="Read and write local files",
        package="@modelcontextprotocol/server-filesystem",
        uvx_package="mcp-server-filesystem",
        category="Starter",
        requires_config=True,
        config_prompt="Directory to allow access to",
        config_type="path",
    ),
    McpServerDefinition(
        name="fetch",
        description="Make HTTP requests to fetch web content",
        package="@modelcontextprotocol/server-fetch",
        uvx_package="mcp-server-fetch",
        category="Starter",
    ),
    McpServerDefinition(
        name="memory",
        description="Persistent key-value storage for context",
        package="@modelcontextprotocol/server-memory",
        uvx_package="mcp-server-memory",
        category="Starter",
    ),
    # Developer Tools
    McpServerDefinition(
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
    McpServerDefinition(
        name="git",
        description="Local git operations",
        package="@modelcontextprotocol/server-git",
        uvx_package="mcp-server-git",
        category="Developer Tools",
    ),
    # Data & Databases
    McpServerDefinition(
        name="sqlite",
        description="Query SQLite databases",
        package="@modelcontextprotocol/server-sqlite",
        uvx_package="mcp-server-sqlite",
        category="Data & Databases",
        requires_config=True,
        config_prompt="Path to SQLite database file",
        config_type="path",
    ),
    McpServerDefinition(
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
    McpServerDefinition(
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
    McpServerDefinition(
        name="puppeteer",
        description="Browser automation",
        package="@modelcontextprotocol/server-puppeteer",
        uvx_package=None,  # No Python equivalent - requires Node.js
        category="Integrations",
    ),
    McpServerDefinition(
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
    McpServerDefinition(
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

# McpServer bundles for quick setup
PROVIDER_BUNDLES: dict[str, list[str]] = {
    "starter": ["filesystem", "fetch", "memory"],
    "developer": ["filesystem", "fetch", "memory", "github", "git"],
    "data": ["filesystem", "fetch", "memory", "sqlite", "postgres"],
}

# Build lookup dict for fast access
_PROVIDERS_BY_NAME: dict[str, McpServerDefinition] = {p.name: p for p in _PROVIDERS}


def get_all_mcp_servers() -> list[McpServerDefinition]:
    """Get all known mcp_servers."""
    return list(_PROVIDERS)


def get_mcp_server(name: str) -> McpServerDefinition | None:
    """Get a mcp_server by name."""
    return _PROVIDERS_BY_NAME.get(name)


def get_mcp_servers_by_category() -> dict[str, list[McpServerDefinition]]:
    """Get mcp_servers grouped by category."""
    result: dict[str, list[McpServerDefinition]] = {}
    for mcp_server in _PROVIDERS:
        if mcp_server.category not in result:
            result[mcp_server.category] = []
        result[mcp_server.category].append(mcp_server)
    return result


def search_mcp_servers(query: str) -> list[McpServerDefinition]:
    """Search mcp_servers by name or description.

    Args:
        query: Search query string

    Returns:
        List of matching mcp_servers
    """
    query_lower = query.lower()
    return [p for p in _PROVIDERS if query_lower in p.name.lower() or query_lower in p.description.lower()]


def get_available_mcp_servers(deps: DependencyStatus | None = None) -> list[McpServerDefinition]:
    """Get mcp_servers that can be installed with current dependencies.

    Args:
        deps: Optional pre-detected dependencies

    Returns:
        List of available mcp_servers
    """
    if deps is None:
        deps = detect_dependencies()
    return [p for p in _PROVIDERS if p.is_available(deps)]


def get_unavailable_mcp_servers(deps: DependencyStatus | None = None) -> list[McpServerDefinition]:
    """Get mcp_servers that cannot be installed due to missing dependencies.

    Args:
        deps: Optional pre-detected dependencies

    Returns:
        List of unavailable mcp_servers
    """
    if deps is None:
        deps = detect_dependencies()
    return [p for p in _PROVIDERS if not p.is_available(deps)]


def get_mcp_servers_by_category_filtered(
    deps: DependencyStatus | None = None,
) -> tuple[dict[str, list[McpServerDefinition]], dict[str, list[McpServerDefinition]]]:
    """Get mcp_servers grouped by category, split into available and unavailable.

    Args:
        deps: Optional pre-detected dependencies

    Returns:
        Tuple of (available_by_category, unavailable_by_category)
    """
    if deps is None:
        deps = detect_dependencies()

    available: dict[str, list[McpServerDefinition]] = {}
    unavailable: dict[str, list[McpServerDefinition]] = {}

    for mcp_server in _PROVIDERS:
        category = mcp_server.category
        if mcp_server.is_available(deps):
            if category not in available:
                available[category] = []
            available[category].append(mcp_server)
        else:
            if category not in unavailable:
                unavailable[category] = []
            unavailable[category].append(mcp_server)

    return available, unavailable


def filter_bundle_by_availability(
    bundle_name: str,
    deps: DependencyStatus | None = None,
) -> tuple[list[str], list[str]]:
    """Filter a bundle to only include available mcp_servers.

    Args:
        bundle_name: Name of the bundle
        deps: Optional pre-detected dependencies

    Returns:
        Tuple of (available_mcp_servers, unavailable_mcp_servers)
    """
    if bundle_name not in PROVIDER_BUNDLES:
        return [], []

    if deps is None:
        deps = detect_dependencies()

    bundle_mcp_servers = PROVIDER_BUNDLES[bundle_name]
    available = []
    unavailable = []

    for name in bundle_mcp_servers:
        mcp_server = get_mcp_server(name)
        if mcp_server and mcp_server.is_available(deps):
            available.append(name)
        else:
            unavailable.append(name)

    return available, unavailable


# legacy aliases
ProviderDefinition = McpServerDefinition
get_provider = get_mcp_server
get_all_providers = get_all_mcp_servers
search_providers = search_mcp_servers
get_providers_by_category = get_mcp_servers_by_category
get_providers_by_category_filtered = get_mcp_servers_by_category_filtered
get_available_providers = get_available_mcp_servers
get_unavailable_providers = get_unavailable_mcp_servers
