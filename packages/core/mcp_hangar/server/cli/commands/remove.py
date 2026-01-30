"""Remove command - Remove providers from configuration.

Removes a provider from the MCP Hangar configuration file and optionally
stops the running provider instance.
"""

import json
from pathlib import Path
from typing import Annotated

import questionary
from rich.console import Console
import typer

from ..errors import ProviderNotFoundError
from ..main import GlobalOptions

console = Console()


def _get_configured_providers(config_path: Path) -> list[str]:
    """Get list of providers from config file.

    Args:
        config_path: Path to config file

    Returns:
        List of provider names
    """
    import yaml

    if not config_path.exists():
        return []

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        return list(config.get("providers", {}).keys())
    except Exception:
        return []


def _remove_from_config(config_path: Path, provider_name: str) -> bool:
    """Remove a provider from the configuration file.

    Args:
        config_path: Path to config file
        provider_name: Name of provider to remove

    Returns:
        True if provider was removed, False if not found
    """
    import yaml

    if not config_path.exists():
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    if "providers" not in config or provider_name not in config["providers"]:
        return False

    del config["providers"][provider_name]

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return True


def _try_stop_provider(provider_name: str) -> bool:
    """Try to stop a running provider instance.

    Args:
        provider_name: Name of provider to stop

    Returns:
        True if provider was stopped, False otherwise
    """
    import httpx

    try:
        for port in [8000, 8080]:
            try:
                response = httpx.post(
                    f"http://localhost:{port}/providers/{provider_name}/stop",
                    timeout=5.0,
                )
                if response.status_code == 200:
                    return True
            except httpx.ConnectError:
                continue
    except Exception:
        pass

    return False


def remove_command(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(
            help="Provider name to remove",
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
            help="Don't stop the running provider instance",
        ),
    ] = False,
):
    """Remove a provider from MCP Hangar configuration.

    Removes the provider from the config file and optionally stops
    any running instance.

    Examples:
        mcp-hangar remove github
        mcp-hangar remove filesystem -y
        mcp-hangar remove postgres --keep-running
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()

    # Determine config path
    config_path = global_opts.config or (Path.home() / ".config" / "mcp-hangar" / "config.yaml")

    # Check if provider exists
    configured = _get_configured_providers(config_path)
    if name not in configured:
        similar = [p for p in configured if name.lower() in p.lower()]
        raise ProviderNotFoundError(name, similar=similar if similar else None)

    # Confirm removal
    if not yes:
        confirm = questionary.confirm(
            f"Remove provider '{name}' from configuration?",
            default=False,
        ).ask()
        if not confirm:
            raise typer.Abort()

    # Stop running instance first
    if not keep_running:
        if _try_stop_provider(name):
            console.print(f"[dim]Stopped running instance of {name}[/dim]")

    # Remove from config
    if _remove_from_config(config_path, name):
        console.print(f"[green]Removed {name} from {config_path}[/green]")
    else:
        console.print(f"[yellow]Provider {name} not found in configuration[/yellow]")

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
