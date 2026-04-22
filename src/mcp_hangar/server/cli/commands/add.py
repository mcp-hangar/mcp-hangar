"""Add command - Add mcp_servers from MCP Registry.

Searches the MCP Registry, installs the mcp_server, prompts for configuration,
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

from ..errors import McpServerNotFoundError
from ..main import GlobalOptions
from ..services import ConfigFileManager, get_all_mcp_servers, get_mcp_server, McpServerDefinition, search_mcp_servers

console = Console()


def _display_search_results(results: list[McpServerDefinition]) -> str | None:
    """Display search results and let user select.

    Args:
        results: List of mcp_server definitions

    Returns:
        Selected mcp_server name or None
    """
    if not results:
        return None

    if len(results) == 1:
        result = results[0]
        console.print(f"\n[bold]Found:[/bold] {result.name} - {result.description}")
        if result.official:
            console.print("[dim]Official Anthropic mcp_server[/dim]")

        confirm = questionary.confirm("Install this mcp_server?", default=True).ask()
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

    console.print("\n[bold]Multiple mcp_servers found:[/bold]")
    console.print(table)

    choices = [questionary.Choice(title=f"{r.name} - {r.description}", value=r.name) for r in results]
    choices.append(questionary.Choice(title="Cancel", value=None))

    return questionary.select("Select a mcp_server to install:", choices=choices).ask()


def _collect_config(mcp_server: McpServerDefinition) -> dict | None:
    """Collect configuration for a mcp_server.

    Args:
        mcp_server: McpServer definition

    Returns:
        Configuration dictionary or None if skipped
    """
    if not mcp_server.requires_config:
        return {}

    # Check if env var is already set
    if mcp_server.env_var and os.environ.get(mcp_server.env_var):
        use_env = questionary.confirm(
            f"Use existing ${mcp_server.env_var} environment variable?",
            default=True,
        ).ask()
        if use_env:
            return {"use_env": mcp_server.env_var}

    console.print(f"\n[dim]{mcp_server.config_prompt}[/dim]")
    if mcp_server.env_var:
        console.print(f"[dim]Tip: You can also set ${mcp_server.env_var} in your shell profile[/dim]")

    if mcp_server.config_type == "secret":
        value = questionary.password(f"{mcp_server.config_prompt}:").ask()
    elif mcp_server.config_type == "path":
        is_dir = mcp_server.config_prompt and "directory" in mcp_server.config_prompt.lower()
        value = questionary.path(f"{mcp_server.config_prompt}:", only_directories=is_dir).ask()
        if value:
            value = str(Path(value).expanduser().resolve())
    else:
        value = questionary.text(f"{mcp_server.config_prompt}:").ask()

    if not value:
        console.print("[yellow]Skipping configuration - mcp_server may not work correctly[/yellow]")
        return {}

    return {"value": value, "env_var": mcp_server.env_var, "config_type": mcp_server.config_type}


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
    except Exception:  # noqa: BLE001 -- fault-barrier: reload attempt must not crash add command
        pass
    return False


def add_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="McpServer name or search query")],
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
    """Add a mcp_server from the MCP Registry.

    Searches for the mcp_server, prompts for configuration, and adds it
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
        results = search_mcp_servers(name)
        if not results:
            raise McpServerNotFoundError(name)

        mcp_server_name = _display_search_results(results)
        if not mcp_server_name:
            raise typer.Abort()
    else:
        mcp_server_name = name

    # Get mcp_server info
    mcp_server = get_mcp_server(mcp_server_name)

    if not mcp_server:
        # Try search as fallback
        results = search_mcp_servers(mcp_server_name)
        if results:
            console.print(f"[yellow]Exact match for '{mcp_server_name}' not found.[/yellow]")
            mcp_server_name = _display_search_results(results)
            if not mcp_server_name:
                raise typer.Abort()
            mcp_server = get_mcp_server(mcp_server_name)
        else:
            all_mcp_servers = get_all_mcp_servers()
            similar = [p.name for p in all_mcp_servers if name[0].lower() == p.name[0].lower()][:3]
            raise McpServerNotFoundError(mcp_server_name, similar=similar if similar else None)

    if not mcp_server:
        raise McpServerNotFoundError(mcp_server_name)

    # Show mcp_server info
    console.print(f"\n[bold]Adding mcp_server:[/bold] {mcp_server.name}")
    console.print(f"[dim]{mcp_server.description}[/dim]")
    console.print(f"[dim]Package: {mcp_server.package}[/dim]")

    # Collect configuration
    mcp_server_config: dict = {}
    if mcp_server.requires_config and not yes:
        result = _collect_config(mcp_server)
        if result is None:
            raise typer.Abort()
        mcp_server_config = result

    # Confirm
    if not yes:
        confirm = questionary.confirm(
            f"Add {mcp_server.name} to {config_mgr.config_path}?",
            default=True,
        ).ask()
        if not confirm:
            raise typer.Abort()

    # Update config file
    config_value = mcp_server_config.get("value")
    use_env = mcp_server_config.get("use_env")
    config_mgr.add_mcp_server(mcp_server, config_value=config_value, use_env=use_env)
    console.print(f"[green]Added {mcp_server.name} to {config_mgr.config_path}[/green]")

    # Try hot reload
    if not no_reload:
        if _try_hot_reload():
            console.print("[green]Server reloaded - mcp_server is now available[/green]")
        else:
            console.print("[dim]Server not running or reload not available[/dim]")
            console.print("Run 'mcp-hangar serve' or restart Claude Desktop to use the new mcp_server")

    # JSON output
    if global_opts.json_output:
        console.print(json.dumps({"added": mcp_server.name, "config_path": str(config_mgr.config_path)}))


__all__ = ["add_command"]
