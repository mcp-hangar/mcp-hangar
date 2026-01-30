"""Add command - Add providers from MCP Registry.

Searches the MCP Registry, installs the provider, prompts for configuration,
and updates the MCP Hangar config file.
"""

import json
import os
from pathlib import Path
from typing import Annotated

import questionary
from rich import box
from rich.console import Console
from rich.table import Table
import typer

from ..errors import ProviderNotFoundError
from ..main import GlobalOptions

console = Console()

# Well-known providers with their configurations
KNOWN_PROVIDERS = {
    "filesystem": {
        "description": "Read and write local files",
        "package": "@anthropic/mcp-server-filesystem",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "Directory to allow access to",
        "config_type": "path",
    },
    "fetch": {
        "description": "Make HTTP requests",
        "package": "@anthropic/mcp-server-fetch",
        "install_type": "npx",
        "requires_config": False,
    },
    "memory": {
        "description": "Persistent key-value storage",
        "package": "@anthropic/mcp-server-memory",
        "install_type": "npx",
        "requires_config": False,
    },
    "github": {
        "description": "GitHub repos, issues, PRs",
        "package": "@anthropic/mcp-server-github",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "GitHub personal access token",
        "config_type": "secret",
        "env_var": "GITHUB_TOKEN",
    },
    "git": {
        "description": "Local git operations",
        "package": "@anthropic/mcp-server-git",
        "install_type": "npx",
        "requires_config": False,
    },
    "sqlite": {
        "description": "Query SQLite databases",
        "package": "@anthropic/mcp-server-sqlite",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "Path to SQLite database file",
        "config_type": "path",
    },
    "postgres": {
        "description": "Query PostgreSQL databases",
        "package": "@anthropic/mcp-server-postgres",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "PostgreSQL connection string",
        "config_type": "secret",
        "env_var": "DATABASE_URL",
    },
    "slack": {
        "description": "Slack workspace integration",
        "package": "@anthropic/mcp-server-slack",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "Slack bot token",
        "config_type": "secret",
        "env_var": "SLACK_BOT_TOKEN",
    },
    "puppeteer": {
        "description": "Browser automation",
        "package": "@anthropic/mcp-server-puppeteer",
        "install_type": "npx",
        "requires_config": False,
    },
    "brave-search": {
        "description": "Brave Search API",
        "package": "@anthropic/mcp-server-brave-search",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "Brave Search API key",
        "config_type": "secret",
        "env_var": "BRAVE_API_KEY",
    },
    "google-maps": {
        "description": "Google Maps API",
        "package": "@anthropic/mcp-server-google-maps",
        "install_type": "npx",
        "requires_config": True,
        "config_prompt": "Google Maps API key",
        "config_type": "secret",
        "env_var": "GOOGLE_MAPS_API_KEY",
    },
}


def _search_registry(query: str) -> list[dict]:
    """Search the MCP Registry for providers.

    Args:
        query: Search query string

    Returns:
        List of matching provider definitions
    """
    # First, search known providers
    results = []
    query_lower = query.lower()

    for name, info in KNOWN_PROVIDERS.items():
        if query_lower in name or query_lower in info.get("description", "").lower():
            results.append(
                {
                    "name": name,
                    "description": info.get("description", ""),
                    "package": info.get("package"),
                    "official": True,
                    **info,
                }
            )

    # TODO: In the future, also query the actual MCP Registry API
    # try:
    #     import httpx
    #     response = httpx.get(f"https://registry.mcp.io/search?q={query}", timeout=10)
    #     if response.status_code == 200:
    #         registry_results = response.json().get("providers", [])
    #         results.extend(registry_results)
    # except Exception:
    #     pass

    return results


def _get_provider_info(name: str) -> dict | None:
    """Get information about a specific provider.

    Args:
        name: Provider name

    Returns:
        Provider definition or None if not found
    """
    if name in KNOWN_PROVIDERS:
        return {"name": name, **KNOWN_PROVIDERS[name]}

    # Search registry
    results = _search_registry(name)
    for result in results:
        if result["name"] == name:
            return result

    return None


def _display_search_results(results: list[dict]) -> str | None:
    """Display search results and let user select.

    Args:
        results: List of provider definitions

    Returns:
        Selected provider name or None
    """
    if not results:
        return None

    if len(results) == 1:
        # Single match - confirm
        result = results[0]
        console.print(f"\n[bold]Found:[/bold] {result['name']} - {result.get('description', '')}")

        if result.get("official"):
            console.print("[dim]Official Anthropic provider[/dim]")

        confirm = questionary.confirm("Install this provider?", default=True).ask()
        return result["name"] if confirm else None

    # Multiple matches - show table and let user choose
    table = Table(box=box.ROUNDED, show_header=True)
    table.add_column("#", width=3)
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("", width=8)

    for i, result in enumerate(results, 1):
        badge = "[green]official[/green]" if result.get("official") else ""
        table.add_row(
            str(i),
            result["name"],
            result.get("description", ""),
            badge,
        )

    console.print("\n[bold]Multiple providers found:[/bold]")
    console.print(table)

    choices = [questionary.Choice(title=f"{r['name']} - {r.get('description', '')}", value=r["name"]) for r in results]
    choices.append(questionary.Choice(title="Cancel", value=None))

    selected = questionary.select(
        "Select a provider to install:",
        choices=choices,
    ).ask()

    return selected


def _collect_config(provider_info: dict) -> dict | None:
    """Collect configuration for a provider.

    Args:
        provider_info: Provider definition

    Returns:
        Configuration dictionary or None if skipped
    """
    if not provider_info.get("requires_config"):
        return {}

    config_type = provider_info.get("config_type", "string")
    config_prompt = provider_info.get("config_prompt", "Configuration value")
    env_var = provider_info.get("env_var")

    # Check if env var is already set
    if env_var and os.environ.get(env_var):
        use_env = questionary.confirm(
            f"Use existing ${env_var} environment variable?",
            default=True,
        ).ask()

        if use_env:
            return {"use_env": env_var}

    console.print(f"\n[dim]{config_prompt}[/dim]")
    if env_var:
        console.print(f"[dim]Tip: You can also set ${env_var} in your shell profile[/dim]")

    if config_type == "secret":
        value = questionary.password(f"{config_prompt}:").ask()
    elif config_type == "path":
        value = questionary.path(
            f"{config_prompt}:",
            only_directories=config_prompt.lower().find("directory") >= 0,
        ).ask()
        if value:
            value = str(Path(value).expanduser().resolve())
    else:
        value = questionary.text(f"{config_prompt}:").ask()

    if not value:
        console.print("[yellow]Skipping configuration - provider may not work correctly[/yellow]")
        return {}

    return {
        "value": value,
        "env_var": env_var,
        "config_type": config_type,
    }


def _update_config_file(
    config_path: Path,
    provider_name: str,
    provider_info: dict,
    provider_config: dict,
):
    """Add provider to the configuration file.

    Args:
        config_path: Path to config file
        provider_name: Name of the provider
        provider_info: Provider definition
        provider_config: Collected configuration
    """
    import yaml

    # Load existing config or create new
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    # Ensure providers section exists
    if "providers" not in config:
        config["providers"] = {}

    # Build provider config
    package = provider_info.get("package", f"@anthropic/mcp-server-{provider_name}")
    install_type = provider_info.get("install_type", "npx")

    provider_entry = {
        "mode": "subprocess",
        "idle_ttl_s": 300,
    }

    if install_type == "npx":
        provider_entry["command"] = ["npx", "-y", package]
    elif install_type == "uvx":
        provider_entry["command"] = ["uvx", package]
    else:
        provider_entry["command"] = [package]

    # Add args if path-based config
    if provider_config.get("config_type") == "path" and provider_config.get("value"):
        provider_entry["args"] = [provider_config["value"]]

    # Add environment variables
    if provider_config.get("use_env"):
        provider_entry["env"] = {provider_config["use_env"]: f"${{{provider_config['use_env']}}}"}
    elif provider_config.get("value") and provider_config.get("env_var"):
        env_var = provider_config["env_var"]
        # Store the actual value - in production this should be env var reference
        provider_entry["env"] = {env_var: provider_config["value"]}

    config["providers"][provider_name] = provider_entry

    # Write back
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def _try_hot_reload() -> bool:
    """Try to trigger hot-reload of the running server.

    Returns:
        True if reload was triggered, False otherwise
    """
    import httpx

    try:
        for port in [8000, 8080]:
            try:
                response = httpx.post(
                    f"http://localhost:{port}/reload",
                    timeout=5.0,
                )
                if response.status_code == 200:
                    return True
            except httpx.ConnectError:
                continue
    except Exception:
        pass

    return False


def add_command(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(
            help="Provider name or search query",
        ),
    ],
    search: Annotated[
        bool,
        typer.Option(
            "--search",
            "-s",
            help="Search the registry instead of exact match",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompts",
        ),
    ] = False,
    no_reload: Annotated[
        bool,
        typer.Option(
            "--no-reload",
            help="Don't try to hot-reload the running server",
        ),
    ] = False,
):
    """Add a provider from the MCP Registry.

    Searches for the provider, prompts for configuration, and adds it
    to your MCP Hangar config file.

    Examples:
        mcp-hangar add github
        mcp-hangar add --search database
        mcp-hangar add filesystem -y
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()

    # Determine config path
    config_path = global_opts.config or (Path.home() / ".config" / "mcp-hangar" / "config.yaml")

    # Search mode
    if search:
        results = _search_registry(name)
        if not results:
            raise ProviderNotFoundError(name)

        provider_name = _display_search_results(results)
        if not provider_name:
            raise typer.Abort()
    else:
        # Exact match
        provider_name = name

    # Get provider info
    provider_info = _get_provider_info(provider_name)

    if not provider_info:
        # Try search as fallback
        results = _search_registry(provider_name)
        if results:
            console.print(f"[yellow]Exact match for '{provider_name}' not found.[/yellow]")
            provider_name = _display_search_results(results)
            if not provider_name:
                raise typer.Abort()
            provider_info = _get_provider_info(provider_name)
        else:
            similar = [n for n in KNOWN_PROVIDERS.keys() if name[0].lower() == n[0].lower()][:3]
            raise ProviderNotFoundError(provider_name, similar=similar if similar else None)

    if not provider_info:
        raise ProviderNotFoundError(provider_name)

    # Show provider info
    console.print(f"\n[bold]Adding provider:[/bold] {provider_name}")
    console.print(f"[dim]{provider_info.get('description', '')}[/dim]")
    if provider_info.get("package"):
        console.print(f"[dim]Package: {provider_info['package']}[/dim]")

    # Collect configuration
    provider_config = {}
    if provider_info.get("requires_config") and not yes:
        provider_config = _collect_config(provider_info)
        if provider_config is None:
            raise typer.Abort()

    # Confirm
    if not yes:
        confirm = questionary.confirm(
            f"Add {provider_name} to {config_path}?",
            default=True,
        ).ask()
        if not confirm:
            raise typer.Abort()

    # Update config file
    _update_config_file(config_path, provider_name, provider_info, provider_config)
    console.print(f"[green]Added {provider_name} to {config_path}[/green]")

    # Try hot reload
    if not no_reload:
        if _try_hot_reload():
            console.print("[green]Server reloaded - provider is now available[/green]")
        else:
            console.print("[dim]Server not running or reload not available[/dim]")
            console.print("Run 'mcp-hangar serve' or restart Claude Desktop to use the new provider")

    # JSON output
    if global_opts.json_output:
        console.print(
            json.dumps(
                {
                    "added": provider_name,
                    "config_path": str(config_path),
                }
            )
        )


__all__ = ["add_command"]
