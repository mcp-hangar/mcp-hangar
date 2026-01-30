"""Status command - Provider health dashboard.

Shows the health and status of all configured providers with:
- Color-coded state indicators
- Tool counts and metadata
- Memory usage and uptime
- Watch mode for real-time updates
"""

import json
from pathlib import Path
import time
from typing import Annotated

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
import typer

from ..main import GlobalOptions

console = Console()


# State colors for display
STATE_COLORS = {
    "READY": "green",
    "COLD": "dim",
    "INITIALIZING": "cyan",
    "DEGRADED": "yellow",
    "DEAD": "red",
}

STATE_ICONS = {
    "READY": "[green]OK[/green]",
    "COLD": "[dim]--[/dim]",
    "INITIALIZING": "[cyan]..[/cyan]",
    "DEGRADED": "[yellow]!![/yellow]",
    "DEAD": "[red]XX[/red]",
}


def _get_status_from_server() -> dict | None:
    """Try to get status from running MCP Hangar server.

    Returns:
        Status dictionary or None if server not reachable.
    """
    # Try to connect to the server via IPC or HTTP
    # For now, we'll try the HTTP endpoint if server is running
    import httpx

    try:
        # Try common ports
        for port in [8000, 8080]:
            try:
                response = httpx.get(
                    f"http://localhost:{port}/health",
                    timeout=2.0,
                )
                if response.status_code == 200:
                    return response.json()
            except httpx.ConnectError:
                continue
    except Exception:
        pass

    return None


def _get_status_from_config(config_path: Path | None) -> dict:
    """Get status from configuration file when server is not running.

    Args:
        config_path: Path to config file, or None to search default locations

    Returns:
        Status dictionary with providers in COLD state
    """
    import yaml

    # Search for config file
    search_paths = [
        config_path,
        Path.home() / ".config" / "mcp-hangar" / "config.yaml",
        Path("config.yaml"),
    ]

    config = None
    used_path = None

    for path in search_paths:
        if path and path.exists():
            try:
                with open(path) as f:
                    config = yaml.safe_load(f)
                used_path = path
                break
            except Exception:
                continue

    if not config:
        return {
            "server_running": False,
            "config_path": None,
            "providers": [],
            "error": "No configuration found",
        }

    # Build provider list from config
    providers = []
    for name, provider_config in config.get("providers", {}).items():
        providers.append(
            {
                "name": name,
                "state": "COLD",
                "mode": provider_config.get("mode", "subprocess"),
                "health_pct": None,
                "tools_count": None,
                "last_used": None,
                "memory_mb": None,
                "uptime_s": None,
            }
        )

    return {
        "server_running": False,
        "config_path": str(used_path),
        "providers": providers,
    }


def _format_uptime(seconds: int | None) -> str:
    """Format uptime in human-readable format."""
    if seconds is None:
        return "-"

    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    else:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def _format_memory(mb: float | None) -> str:
    """Format memory usage."""
    if mb is None:
        return "-"
    if mb < 1:
        return f"{mb * 1024:.0f}KB"
    return f"{mb:.1f}MB"


def _create_status_table(status: dict, show_details: bool = False) -> Table:
    """Create the status table for display.

    Args:
        status: Status dictionary from server or config
        show_details: Whether to show additional columns

    Returns:
        Rich Table object
    """
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        title="MCP Hangar Status" if not status.get("server_running") else None,
    )

    # Add columns
    table.add_column("", width=3)  # Status icon
    table.add_column("Provider", style="bold")
    table.add_column("State", justify="center")
    table.add_column("Health", justify="right")
    table.add_column("Tools", justify="right")

    if show_details:
        table.add_column("Mode")
        table.add_column("Memory", justify="right")
        table.add_column("Uptime", justify="right")
        table.add_column("Last Used")

    # Add rows for each provider
    for provider in status.get("providers", []):
        state = provider.get("state", "COLD")
        state_color = STATE_COLORS.get(state, "white")
        state_icon = STATE_ICONS.get(state, "??")

        health_pct = provider.get("health_pct")
        health_str = f"{health_pct}%" if health_pct is not None else "-"
        if health_pct is not None:
            if health_pct >= 90:
                health_str = f"[green]{health_str}[/green]"
            elif health_pct >= 50:
                health_str = f"[yellow]{health_str}[/yellow]"
            else:
                health_str = f"[red]{health_str}[/red]"

        tools_count = provider.get("tools_count")
        tools_str = str(tools_count) if tools_count is not None else "-"

        row = [
            state_icon,
            provider["name"],
            f"[{state_color}]{state}[/{state_color}]",
            health_str,
            tools_str,
        ]

        if show_details:
            row.extend(
                [
                    provider.get("mode", "-"),
                    _format_memory(provider.get("memory_mb")),
                    _format_uptime(provider.get("uptime_s")),
                    provider.get("last_used") or "-",
                ]
            )

        table.add_row(*row)

    return table


def _create_summary_panel(status: dict) -> Panel:
    """Create a summary panel for the status display."""
    providers = status.get("providers", [])
    total = len(providers)

    if total == 0:
        return Panel(
            "[dim]No providers configured[/dim]",
            title="Summary",
            border_style="dim",
        )

    ready = sum(1 for p in providers if p.get("state") == "READY")
    degraded = sum(1 for p in providers if p.get("state") == "DEGRADED")
    dead = sum(1 for p in providers if p.get("state") == "DEAD")

    parts = []
    if status.get("server_running"):
        parts.append("[green]Server running[/green]")
    else:
        parts.append("[yellow]Server not running[/yellow]")

    parts.append(f"Providers: {total}")

    if ready:
        parts.append(f"[green]Ready: {ready}[/green]")
    if degraded:
        parts.append(f"[yellow]Degraded: {degraded}[/yellow]")
    if dead:
        parts.append(f"[red]Dead: {dead}[/red]")

    return Panel(
        " | ".join(parts),
        border_style="blue",
    )


def _get_provider_details(name: str, status: dict) -> dict | None:
    """Get detailed information for a single provider."""
    for provider in status.get("providers", []):
        if provider.get("name") == name:
            return provider
    return None


def _display_provider_details(name: str, status: dict):
    """Display detailed information for a single provider."""
    provider = _get_provider_details(name, status)

    if not provider:
        console.print(f"[red]Provider '{name}' not found[/red]")
        return

    state = provider.get("state", "COLD")
    state_color = STATE_COLORS.get(state, "white")

    # Header
    console.print(
        Panel(
            f"[bold]{name}[/bold] - [{state_color}]{state}[/{state_color}]",
            border_style=state_color,
        )
    )

    # Info table
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("Mode", provider.get("mode", "-"))
    table.add_row("Health", f"{provider.get('health_pct', '-')}%")
    table.add_row("Tools", str(provider.get("tools_count", "-")))
    table.add_row("Memory", _format_memory(provider.get("memory_mb")))
    table.add_row("Uptime", _format_uptime(provider.get("uptime_s")))
    table.add_row("Last Used", provider.get("last_used") or "-")

    console.print(table)

    # Tools list if available
    tools = provider.get("tools", [])
    if tools:
        console.print("\n[bold]Available Tools:[/bold]")
        for tool in tools:
            desc = tool.get("description", "")
            if desc:
                console.print(f"  [green]+[/green] {tool['name']} - [dim]{desc}[/dim]")
            else:
                console.print(f"  [green]+[/green] {tool['name']}")

    # Recent health checks if available
    health_history = provider.get("health_history", [])
    if health_history:
        console.print("\n[bold]Recent Health Checks:[/bold]")
        for check in health_history[-5:]:
            result = check.get("result", "unknown")
            timestamp = check.get("timestamp", "")
            if result == "passed":
                console.print(f"  [green]PASS[/green] {timestamp}")
            else:
                console.print(f"  [red]FAIL[/red] {timestamp} - {check.get('error', '')}")


def status_command(
    ctx: typer.Context,
    provider: Annotated[
        str | None,
        typer.Argument(
            help="Show detailed status for a specific provider",
        ),
    ] = None,
    watch: Annotated[
        bool,
        typer.Option(
            "--watch",
            "-w",
            help="Continuously update the display",
        ),
    ] = False,
    interval: Annotated[
        float,
        typer.Option(
            "--interval",
            "-i",
            help="Update interval in seconds (with --watch)",
        ),
    ] = 2.0,
    details: Annotated[
        bool,
        typer.Option(
            "--details",
            "-d",
            help="Show additional columns (mode, memory, uptime)",
        ),
    ] = False,
):
    """Show status of all providers.

    Displays a table with provider states, health percentages, and tool counts.
    Use --watch for real-time updates.

    Examples:
        mcp-hangar status
        mcp-hangar status --watch
        mcp-hangar status my-provider
        mcp-hangar status --details
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()

    # Get initial status
    status = _get_status_from_server()
    if status is None:
        status = _get_status_from_config(global_opts.config)

    # JSON output mode
    if global_opts.json_output:
        console.print(json.dumps(status, indent=2))
        return

    # Single provider details
    if provider:
        _display_provider_details(provider, status)
        return

    # Watch mode with live updates
    if watch:
        with Live(console=console, refresh_per_second=1) as live:
            try:
                while True:
                    status = _get_status_from_server()
                    if status is None:
                        status = _get_status_from_config(global_opts.config)

                    # Create display
                    table = _create_status_table(status, show_details=details)
                    summary = _create_summary_panel(status)

                    from rich.console import Group

                    live.update(Group(summary, table))

                    time.sleep(interval)
            except KeyboardInterrupt:
                pass
        return

    # Standard display
    summary = _create_summary_panel(status)
    table = _create_status_table(status, show_details=details)

    console.print(summary)
    console.print(table)

    # Show config path if server not running
    if not status.get("server_running") and status.get("config_path"):
        console.print(f"\n[dim]Config: {status['config_path']}[/dim]")
        console.print("[dim]Run 'mcp-hangar serve' to start the server[/dim]")


__all__ = ["status_command"]
