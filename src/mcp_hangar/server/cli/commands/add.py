"""Add command - Add MCP servers from MCP Registry.

Searches the MCP Registry, installs the MCP server, prompts for configuration,
and updates the MCP Hangar config file.
"""

import json
import os
from pathlib import Path
from typing import Annotated, cast

import questionary
import typer
from rich import box
from rich.console import Console
from rich.table import Table

from ..config_path import resolve_config_path
from ..errors import McpServerNotFoundError
from ..main import GlobalOptions
from ..services import (
    ConfigFileManager,
    McpServerDefinition,
    gateway_api,
    get_all_mcp_servers,
    get_mcp_server,
    search_mcp_servers,
)

console = Console()


def _display_search_results(results: list[McpServerDefinition]) -> str | None:
    """Display search results and let user select.

    Args:
        results: List of MCP server definitions

    Returns:
        Selected MCP server name or None
    """
    if not results:
        return None

    if len(results) == 1:
        result = results[0]
        console.print(f"\n[bold]Found:[/bold] {result.name} - {result.description}")
        if result.official:
            console.print("[dim]Official first-party MCP server[/dim]")

        confirm = questionary.confirm("Install this MCP server?", default=True).ask()
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

    console.print("\n[bold]Multiple MCP servers found:[/bold]")
    console.print(table)

    choices = [questionary.Choice(title=f"{r.name} - {r.description}", value=r.name) for r in results]
    choices.append(questionary.Choice(title="Cancel", value=None))

    return cast(str | None, questionary.select("Select an MCP server to install:", choices=choices).ask())


def _collect_config(mcp_server: McpServerDefinition) -> dict | None:
    """Collect configuration for an MCP server.

    Args:
        mcp_server: MCP server definition

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
        is_dir = bool(mcp_server.config_prompt and "directory" in mcp_server.config_prompt.lower())
        value = questionary.path(f"{mcp_server.config_prompt}:", only_directories=is_dir).ask()
        if value:
            value = str(Path(value).expanduser().resolve())
    else:
        value = questionary.text(f"{mcp_server.config_prompt}:").ask()

    if not value:
        console.print("[yellow]Skipping configuration - MCP server may not work correctly[/yellow]")
        return {}

    return {"value": value, "env_var": mcp_server.env_var, "config_type": mcp_server.config_type}


def _try_hot_reload() -> gateway_api.GatewayReply:
    """Ask the running gateway to reload its configuration."""
    return gateway_api.call("POST", "/api/config/reload")


def _report_hot_reload(reply: gateway_api.GatewayReply) -> None:
    """Say what the reload did, and why when it did not."""
    if reply.ok:
        console.print("[green]Server reloaded - MCP server is now available[/green]")
    elif reply.problem():
        console.print(f"[yellow]Not reloaded: {reply.problem()}[/yellow]")
        console.print("Reload the gateway yourself, or restart it, to use the new MCP server")
    else:
        console.print("[dim]No gateway answered, so there is nothing to reload[/dim]")
        console.print("Run 'mcp-hangar serve' or restart Claude Desktop to use the new MCP server")


def _collect_config_unless(mcp_server: McpServerDefinition, yes: bool) -> dict:
    """The server's configuration, asked for unless *yes* or it needs none."""
    if not mcp_server.requires_config or yes:
        return {}
    result = _collect_config(mcp_server)
    if result is None:
        raise typer.Abort()
    return result


def _already_configured(
    config_mgr: ConfigFileManager,
    mcp_server: McpServerDefinition,
    config_value: str | None,
    use_env: str | None,
    *,
    replace: bool,
    json_output: bool = False,
) -> bool:
    """Whether the file already holds this exact entry; refuse a different one unless *replace*.

    A server the file already has is not replaced behind the operator's back: its
    pins, policy, args and env are not in the registry entry, and writing the
    entry over it used to drop them all without a word (#2042).

    Raises:
        typer.Exit: The file holds a different entry and *replace* is not set.
    """
    existing = config_mgr.configured_entry(mcp_server.name)
    if existing is None:
        return False
    proposed = config_mgr.build_mcp_server_entry(mcp_server, config_value=config_value, use_env=use_env)
    if existing == proposed:
        console.print(f"[green]{mcp_server.name} is already configured as the registry describes it.[/green]")
        if json_output:
            console.print(json.dumps({"added": None, "unchanged": mcp_server.name}))
        return True
    dropped = sorted(set(existing) - set(proposed))
    changed = sorted(key for key in set(existing) & set(proposed) if existing[key] != proposed[key])
    detail = ", ".join([*(f"drops {key}" for key in dropped), *(f"changes {key}" for key in changed)])
    if not replace:
        console.print(
            f"[red]{mcp_server.name} is already in {config_mgr.config_path}.[/red] Adding it again would "
            f"replace its entry: {detail}.\nRun `mcp-hangar add {mcp_server.name} --replace` to do that, "
            "or edit the file."
        )
        raise typer.Exit(1)
    console.print(f"[yellow]Replacing {mcp_server.name}: {detail}.[/yellow]")
    return False


def add_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="MCP server name or search query")],
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
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Replace a server the config already has, dropping what the registry entry lacks (pins, env, args)",
        ),
    ] = False,
):
    """Add an MCP server from the MCP Registry.

    Searches for the MCP server, prompts for configuration, and adds it
    to your MCP Hangar config file.

    Examples:
        mcp-hangar add git
        mcp-hangar add --search fetch
        mcp-hangar add filesystem -y
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()
    config_mgr = ConfigFileManager(resolve_config_path(global_opts.config).path)

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
    console.print(f"\n[bold]Adding MCP server:[/bold] {mcp_server.name}")
    console.print(f"[dim]{mcp_server.description}[/dim]")
    console.print(f"[dim]Package: {mcp_server.package}[/dim]")

    # Collect configuration
    mcp_server_config = _collect_config_unless(mcp_server, yes)

    config_value = mcp_server_config.get("value")
    use_env = mcp_server_config.get("use_env")
    if _already_configured(
        config_mgr, mcp_server, config_value, use_env, replace=replace, json_output=global_opts.json_output
    ):
        return

    # Confirm
    if not yes:
        confirm = questionary.confirm(
            f"Add {mcp_server.name} to {config_mgr.config_path}?",
            default=True,
        ).ask()
        if not confirm:
            raise typer.Abort()

    # Update config file
    config_mgr.add_mcp_server(mcp_server, config_value=config_value, use_env=use_env)
    console.print(f"[green]Added {mcp_server.name} to {config_mgr.config_path}[/green]")
    if config_value and mcp_server.env_var and mcp_server.config_type == "secret":
        # The value is not in the file. Say so, and say what to do with it.
        console.print(
            f"[yellow]The secret was not written to the file.[/yellow] The entry reads ${mcp_server.env_var}: "
            f"set it in the environment that starts Hangar (your shell profile, or the client's env block)."
        )

    # Try hot reload
    if not no_reload:
        _report_hot_reload(_try_hot_reload())

    # JSON output
    if global_opts.json_output:
        console.print(json.dumps({"added": mcp_server.name, "config_path": str(config_mgr.config_path)}))


__all__ = ["add_command"]
