"""Init command - Interactive setup wizard for MCP Hangar.

This command provides the "5-minute experience" for new users:
1. Detect Claude Desktop installation
2. Present provider selection with bundles
3. Collect required configuration
4. Generate MCP Hangar config file
5. Update Claude Desktop config
6. Show completion summary with next steps
"""

import os
from pathlib import Path
from typing import Annotated

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from ..errors import CLIError, PermissionError
from ..main import GlobalOptions
from ..services import (
    ClaudeDesktopManager,
    ConfigFileManager,
    get_provider,
    get_providers_by_category,
    PROVIDER_BUNDLES,
    ProviderDefinition,
)

app = typer.Typer(
    name="init",
    help="Initialize MCP Hangar with interactive setup wizard",
    invoke_without_command=True,
)

console = Console()


def _prompt_provider_selection() -> list[str]:
    """Interactive provider selection with categories.

    Returns:
        List of selected provider names.
    """
    categories = get_providers_by_category()
    selected = []

    console.print("\n[bold]Select providers to enable:[/bold]")
    console.print("[dim]Use arrow keys and space to select, Enter to confirm[/dim]\n")

    for category, providers in categories.items():
        is_starter = category == "Starter"
        choices = [
            questionary.Choice(
                title=f"{p.name} - {p.description}",
                value=p.name,
                checked=is_starter,  # Pre-select starter providers
            )
            for p in providers
        ]

        if choices:
            category_label = f"{category} (recommended for everyone)" if is_starter else category
            category_selected = questionary.checkbox(
                category_label,
                choices=choices,
            ).ask()

            if category_selected is None:
                raise typer.Abort()

            selected.extend(category_selected)

    return selected


def _collect_provider_config(provider: ProviderDefinition) -> dict | None:
    """Collect configuration for a provider that requires it.

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
            f"{provider.name}: Use existing ${provider.env_var} environment variable?",
            default=True,
        ).ask()

        if use_env:
            return {"use_env": provider.env_var}

    # Prompt based on config type
    if provider.config_type == "secret":
        console.print(f"\n[dim]For {provider.name}: {provider.config_prompt}[/dim]")
        if provider.env_var:
            console.print(f"[dim]Tip: You can also set ${provider.env_var} in your shell profile[/dim]")

        value = questionary.password(
            f"{provider.config_prompt} (or press Enter to skip):",
        ).ask()

        if not value:
            msg = f"Skipping {provider.name} - configure later with 'mcp-hangar configure {provider.name}'"
            console.print(f"[yellow]{msg}[/yellow]")
            return None

        return {"value": value, "env_var": provider.env_var}

    elif provider.config_type == "path":
        default_path = str(Path.home())
        value = questionary.path(
            f"{provider.config_prompt}:",
            default=default_path,
            only_directories=True,
        ).ask()

        if not value:
            return None

        return {"path": str(Path(value).expanduser().resolve())}

    else:
        value = questionary.text(f"{provider.config_prompt}:").ask()
        return {"value": value} if value else None


def _show_completion_summary(
    providers: list[str],
    hangar_config_path: Path,
    claude_config_path: Path | None,
    backup_path: Path | None,
):
    """Display completion summary with next steps."""
    console.print()

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column("Item", style="bold")
    table.add_column("Value")

    table.add_row("Providers configured", str(len(providers)))
    table.add_row("MCP Hangar config", str(hangar_config_path))
    if claude_config_path:
        table.add_row("Claude Desktop config", str(claude_config_path))
    if backup_path:
        table.add_row("Backup created", str(backup_path))

    console.print(Panel(table, title="[bold green]Setup Complete[/bold green]", border_style="green"))

    if providers:
        console.print("\n[bold]Enabled providers:[/bold]")
        for name in providers:
            console.print(f"  [green]+[/green] {name}")

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. [bold]Restart Claude Desktop[/bold] to activate the new configuration")
    console.print("  2. Run [bold]mcp-hangar status[/bold] to verify providers are healthy")
    console.print("  3. Run [bold]mcp-hangar add <provider>[/bold] to add more providers later")
    console.print("\n[dim]Need help? Visit https://docs.mcp-hangar.io[/dim]")


@app.callback(invoke_without_command=True)
def init_command(
    ctx: typer.Context,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", "-y", help="Run without prompts, using defaults"),
    ] = False,
    bundle: Annotated[
        str | None,
        typer.Option("--bundle", "-b", help="Provider bundle to install: starter, developer, data"),
    ] = None,
    providers_opt: Annotated[
        str | None,
        typer.Option("--providers", help="Comma-separated list of providers to install"),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config-path", help="Custom path for MCP Hangar config file"),
    ] = None,
    claude_config_path: Annotated[
        Path | None,
        typer.Option("--claude-config", help="Custom path to Claude Desktop config"),
    ] = None,
    skip_claude: Annotated[
        bool,
        typer.Option("--skip-claude", help="Skip Claude Desktop config modification"),
    ] = False,
    reset: Annotated[
        bool,
        typer.Option("--reset", help="Reset existing configuration"),
    ] = False,
):
    """Initialize MCP Hangar with interactive setup wizard.

    This wizard will:
    - Detect your Claude Desktop installation
    - Help you select which MCP providers to enable
    - Create a configuration file
    - Update Claude Desktop to use MCP Hangar

    Examples:
        mcp-hangar init
        mcp-hangar init --bundle starter
        mcp-hangar init --providers filesystem,github,sqlite
        mcp-hangar init --non-interactive --bundle developer
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()

    # Initialize managers
    effective_config_path = config_path or global_opts.config or ConfigFileManager.DEFAULT_CONFIG_PATH
    config_mgr = ConfigFileManager(effective_config_path)
    claude_mgr = ClaudeDesktopManager(claude_config_path)

    # Welcome message
    if not non_interactive:
        console.print(
            Panel(
                "[bold]Welcome to MCP Hangar![/bold]\n\n"
                "This wizard will help you set up MCP Hangar in just a few minutes.\n"
                "MCP Hangar manages your MCP providers so Claude Desktop only needs\n"
                "to connect to a single process.",
                title="MCP Hangar Setup",
                border_style="blue",
            )
        )

    # Step 1: Detect Claude Desktop
    console.print("\n[bold]Step 1:[/bold] Detecting Claude Desktop...")

    if claude_mgr.exists():
        console.print(f"  [green]Found:[/green] {claude_mgr.config_path}")
        servers = claude_mgr.get_mcp_servers()
        if servers:
            console.print(f"  [dim]Existing MCP servers: {len(servers)}[/dim]")
    elif not skip_claude:
        if non_interactive:
            console.print("  [yellow]Claude Desktop not found - skipping integration[/yellow]")
            skip_claude = True
        else:
            console.print("  [yellow]Claude Desktop not found[/yellow]")
            proceed = questionary.confirm(
                "Continue without Claude Desktop integration?",
                default=True,
            ).ask()
            if not proceed:
                raise typer.Abort()
            skip_claude = True

    # Step 2: Provider selection
    console.print("\n[bold]Step 2:[/bold] Selecting providers...")

    selected_providers: list[str] = []
    provider_configs: dict[str, dict] = {}

    if providers_opt:
        selected_providers = [p.strip() for p in providers_opt.split(",")]
        console.print(f"  Using providers: {', '.join(selected_providers)}")
    elif bundle:
        if bundle.lower() not in PROVIDER_BUNDLES:
            raise CLIError(
                message=f"Unknown bundle: {bundle}",
                reason=f"Available bundles: {', '.join(PROVIDER_BUNDLES.keys())}",
                suggestions=["Use --bundle=starter, --bundle=developer, or --bundle=data"],
            )
        selected_providers = PROVIDER_BUNDLES[bundle.lower()]
        console.print(f"  Using '{bundle}' bundle: {', '.join(selected_providers)}")
    elif non_interactive:
        selected_providers = PROVIDER_BUNDLES["starter"]
        console.print(f"  Using default providers: {', '.join(selected_providers)}")
    else:
        selected_providers = _prompt_provider_selection()

    if not selected_providers:
        console.print("  [yellow]No providers selected[/yellow]")
        if not non_interactive:
            proceed = questionary.confirm("Continue with empty configuration?", default=False).ask()
            if not proceed:
                raise typer.Abort()

    # Step 3: Collect provider configurations
    if selected_providers and not non_interactive:
        console.print("\n[bold]Step 3:[/bold] Configuring providers...")

        for name in list(selected_providers):
            provider = get_provider(name)
            if provider and provider.requires_config:
                config = _collect_provider_config(provider)
                if config is None:
                    selected_providers.remove(name)
                else:
                    provider_configs[name] = config

    # Step 4: Generate configuration files
    console.print("\n[bold]Step 4:[/bold] Generating configuration...")

    # Backup existing config if present and not resetting
    backup_path = None
    if config_mgr.exists() and not reset:
        if not non_interactive:
            overwrite = questionary.confirm(
                f"Configuration exists at {config_mgr.config_path}. Overwrite?",
                default=False,
            ).ask()
            if not overwrite:
                raise typer.Abort()
        backup_path = config_mgr.backup()
        if backup_path:
            console.print(f"  [dim]Backed up to: {backup_path}[/dim]")

    # Write MCP Hangar config
    provider_defs = [get_provider(name) for name in selected_providers]
    provider_defs = [p for p in provider_defs if p is not None]

    try:
        config_mgr.write_initial_config(provider_defs, provider_configs)
        console.print(f"  [green]Created:[/green] {config_mgr.config_path}")
    except OSError as e:
        raise PermissionError(str(config_mgr.config_path), "write") from e

    # Step 5: Update Claude Desktop config
    claude_backup_path = None
    if not skip_claude and claude_mgr.exists():
        console.print("\n[bold]Step 5:[/bold] Updating Claude Desktop...")

        claude_backup_path = claude_mgr.backup()
        if claude_backup_path:
            console.print(f"  [dim]Backed up to: {claude_backup_path}[/dim]")

        try:
            claude_mgr.update_for_hangar(config_mgr.config_path)
            console.print(f"  [green]Updated:[/green] {claude_mgr.config_path}")
        except OSError as e:
            raise PermissionError(str(claude_mgr.config_path), "write") from e

    # Step 6: Completion summary
    _show_completion_summary(
        providers=selected_providers,
        hangar_config_path=config_mgr.config_path,
        claude_config_path=claude_mgr.config_path if not skip_claude else None,
        backup_path=claude_backup_path or backup_path,
    )


__all__ = ["app", "init_command"]
