"""Remove command - Remove MCP servers from configuration.

Removes an MCP server from the MCP Hangar configuration file and optionally
stops the running MCP server instance.
"""

import json
from pathlib import Path
from typing import Annotated

import questionary
from rich.console import Console
import typer

from ..errors import McpServerNotFoundError
from ..main import GlobalOptions

console = Console()


def _get_configured_mcp_servers(config_path: Path) -> list[str]:
    """Get list of MCP servers from config file.

    Args:
        config_path: Path to config file

    Returns:
        List of MCP server names
    """
    import yaml

    if not config_path.exists():
        return []

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        return list(config.get("mcp_servers", {}).keys())
    except Exception:  # noqa: BLE001 -- fault-barrier: config read must not crash remove command
        return []


def _remove_from_config(config_path: Path, mcp_server_name: str) -> bool:
    """Remove an MCP server from the configuration file.

    Args:
        config_path: Path to config file
        mcp_server_name: Name of MCP server to remove

    Returns:
        True if MCP server was removed, False if not found
    """
    import yaml

    if not config_path.exists():
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    if "mcp_servers" not in config or mcp_server_name not in config["mcp_servers"]:
        return False

    del config["mcp_servers"][mcp_server_name]

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return True


def _try_stop_mcp_server(mcp_server_name: str) -> bool:
    """Try to stop a running MCP server instance.

    Args:
        mcp_server_name: Name of MCP server to stop

    Returns:
        True if MCP server was stopped, False otherwise
    """
    import httpx

    try:
        for port in [8000, 8080]:
            try:
                response = httpx.post(
                    f"http://localhost:{port}/MCP servers/{mcp_server_name}/stop",
                    timeout=5.0,
                )
                if response.status_code == 200:
                    return True
            except httpx.ConnectError:
                continue
    except Exception:  # noqa: BLE001 -- fault-barrier: stop attempt must not crash remove command
        pass

    return False


def remove_command(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(
            help="MCP server name to remove",
        ),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt",
        ),
    ] = False,
    keep_running: Annotated[
        bool,
        typer.Option(
            "--keep-running",
            help="Don't stop the running MCP server instance",
        ),
    ] = False,
):
    """Remove an MCP server from MCP Hangar configuration.

    Removes the MCP server from the config file and optionally stops
    any running instance.

    Examples:
        mcp-hangar remove github
        mcp-hangar remove filesystem -y
        mcp-hangar remove postgres --keep-running
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()

    # Determine config path
    config_path = global_opts.config or (Path.home() / ".config" / "mcp-hangar" / "config.yaml")

    # Check if mcp_server exists
    configured = _get_configured_mcp_servers(config_path)
    if name not in configured:
        similar = [p for p in configured if name.lower() in p.lower()]
        raise McpServerNotFoundError(name, similar=similar if similar else None)

    # Confirm removal
    if not yes:
        confirm = questionary.confirm(
            f"Remove MCP server '{name}' from configuration?",
            default=False,
        ).ask()
        if not confirm:
            raise typer.Abort()

    # Stop running instance first
    if not keep_running:
        if _try_stop_mcp_server(name):
            console.print(f"[dim]Stopped running instance of {name}[/dim]")

    # Remove from config
    if _remove_from_config(config_path, name):
        console.print(f"[green]Removed {name} from {config_path}[/green]")
    else:
        console.print(f"[yellow]MCP server {name} not found in configuration[/yellow]")

    # JSON output
    if global_opts.json_output:
        console.print(
            json.dumps(
                {
                    "removed": name,
                    "config_path": str(config_path),
                }
            )
        )


__all__ = ["remove_command"]
