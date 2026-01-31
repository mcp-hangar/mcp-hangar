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
from ..services import ConfigFileManager, get_all_providers, get_provider, ProviderDefinition, search_providers

console = Console()


def _display_search_results(results: list[ProviderDefinition]) -> str | None:
    """Display search results and let user select.

    Args:
        results: List of provider definitions

    Returns:
        Selected provider name or None
    """
    if not results:
        return None

    if len(results) == 1:
        result = results[0]
        console.print(f"\n[bold]Found:[/bold] {result.name} - {result.description}")
        if result.official:
            console.print("[dim]Official Anthropic provider[/dim]")

        confirm = questionary.confirm("Install this provider?", default=True).ask()
        return result.name if confirm else None

    # Multiple matches - show table
    table = Table(box=box.ROUNDED, show_header=True)
    table.add_column("#", width=3)
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("", width=8)

    for i, result in enumerate(results, 1):
        badge = "[green]official[/green]" if result.official else ""
        table.add_row(str(i), result.name, result.description, badge)

    console.print("\n[bold]Multiple providers found:[/bold]")
    console.print(table)

    choices = [questionary.Choice(title=f"{r.name} - {r.description}", value=r.name) for r in results]
    choices.append(questionary.Choice(title="Cancel", value=None))

    return questionary.select("Select a provider to install:", choices=choices).ask()


def _collect_config(provider: ProviderDefinition) -> dict | None:
    """Collect configuration for a provider.

    Args:
        provider: Provider definition

    Returns:
        Configuration dictionary or None if skipped
    """
    if not provider.requires_config:
        return {}

    # Check if env var is already set
    if provider.env_var and os.environ.get(provider.env_var):
        use_env = questionary.confirm(
            f"Use existing ${provider.env_var} environment variable?",
            default=True,
        ).ask()
        if use_env:
            return {"use_env": provider.env_var}

    console.print(f"\n[dim]{provider.config_prompt}[/dim]")
    if provider.env_var:
        console.print(f"[dim]Tip: You can also set ${provider.env_var} in your shell profile[/dim]")

    if provider.config_type == "secret":
        value = questionary.password(f"{provider.config_prompt}:").ask()
    elif provider.config_type == "path":
        is_dir = provider.config_prompt and "directory" in provider.config_prompt.lower()
        value = questionary.path(f"{provider.config_prompt}:", only_directories=is_dir).ask()
        if value:
            value = str(Path(value).expanduser().resolve())
    else:
        value = questionary.text(f"{provider.config_prompt}:").ask()

    if not value:
        console.print("[yellow]Skipping configuration - provider may not work correctly[/yellow]")
        return {}

    return {"value": value, "env_var": provider.env_var, "config_type": provider.config_type}


def _try_hot_reload() -> bool:
    """Try to trigger hot-reload of the running server."""
    try:
        import httpx

        for port in [8000, 8080]:
            try:
                response = httpx.post(f"http://localhost:{port}/reload", timeout=5.0)
                if response.status_code == 200:
                    return True
            except httpx.ConnectError:
                continue
    except Exception:
        pass
    return False


def add_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Provider name or search query")],
    search: Annotated[
        bool,
        typer.Option("--search", "-s", help="Search the registry instead of exact match"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompts"),
    ] = False,
    no_reload: Annotated[
        bool,
        typer.Option("--no-reload", help="Don't try to hot-reload the running server"),
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
    config_mgr = ConfigFileManager(global_opts.config)

    # Search mode
    if search:
        results = search_providers(name)
        if not results:
            raise ProviderNotFoundError(name)

        provider_name = _display_search_results(results)
        if not provider_name:
            raise typer.Abort()
    else:
        provider_name = name

    # Get provider info
    provider = get_provider(provider_name)

    if not provider:
        # Try search as fallback
        results = search_providers(provider_name)
        if results:
            console.print(f"[yellow]Exact match for '{provider_name}' not found.[/yellow]")
            provider_name = _display_search_results(results)
            if not provider_name:
                raise typer.Abort()
            provider = get_provider(provider_name)
        else:
            all_providers = get_all_providers()
            similar = [p.name for p in all_providers if name[0].lower() == p.name[0].lower()][:3]
            raise ProviderNotFoundError(provider_name, similar=similar if similar else None)

    if not provider:
        raise ProviderNotFoundError(provider_name)

    # Show provider info
    console.print(f"\n[bold]Adding provider:[/bold] {provider.name}")
    console.print(f"[dim]{provider.description}[/dim]")
    console.print(f"[dim]Package: {provider.package}[/dim]")

    # Collect configuration
    provider_config: dict = {}
    if provider.requires_config and not yes:
        result = _collect_config(provider)
        if result is None:
            raise typer.Abort()
        provider_config = result

    # Confirm
    if not yes:
        confirm = questionary.confirm(
            f"Add {provider.name} to {config_mgr.config_path}?",
            default=True,
        ).ask()
        if not confirm:
            raise typer.Abort()

    # Update config file
    config_value = provider_config.get("value")
    use_env = provider_config.get("use_env")
    config_mgr.add_provider(provider, config_value=config_value, use_env=use_env)
    console.print(f"[green]Added {provider.name} to {config_mgr.config_path}[/green]")

    # Try hot reload
    if not no_reload:
        if _try_hot_reload():
            console.print("[green]Server reloaded - provider is now available[/green]")
        else:
            console.print("[dim]Server not running or reload not available[/dim]")
            console.print("Run 'mcp-hangar serve' or restart Claude Desktop to use the new provider")

    # JSON output
    if global_opts.json_output:
        console.print(json.dumps({"added": provider.name, "config_path": str(config_mgr.config_path)}))


__all__ = ["add_command"]
