"""Init command - Interactive setup wizard for MCP Hangar.

This command provides the "5-minute experience" for new users:
0. Detect available runtimes (npx, uvx, docker)
1. Detect Claude Desktop installation
2. Present provider selection with bundles (filtered by available deps)
3. Collect required configuration
4. Generate MCP Hangar config file
5. Smoke test providers
6. Update Claude Desktop config
7. Show completion summary with next steps
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
    DependencyStatus,
    detect_dependencies,
    filter_bundle_by_availability,
    get_install_instructions,
    get_provider,
    get_providers_by_category_filtered,
    PROVIDER_BUNDLES,
    ProviderDefinition,
    run_smoke_test,
)


# Existing config handling options
class ExistingConfigAction:
    """Actions for handling existing configuration."""

    MERGE = "merge"
    BACKUP_OVERWRITE = "backup"
    ABORT = "abort"


app = typer.Typer(
    name="init",
    help="Initialize MCP Hangar with interactive setup wizard",
    invoke_without_command=True,
)

console = Console()


def _show_dependency_status(deps: DependencyStatus) -> None:
    """Display detected dependencies status."""
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Runtime", style="bold")
    table.add_column("Status")
    table.add_column("Path", style="dim")

    for runtime in [deps.npx, deps.uvx, deps.docker, deps.podman]:
        if runtime.available:
            status = "[green]available[/green]"
            path = runtime.path or ""
        else:
            status = "[dim]not found[/dim]"
            path = ""
        table.add_row(runtime.name, status, path)

    console.print(table)


def _check_dependencies_or_exit(deps: DependencyStatus, non_interactive: bool) -> None:
    """Check if any runtime is available, exit with instructions if not."""
    if deps.has_any:
        return

    console.print("\n[bold red]No supported runtimes found![/bold red]\n")
    console.print("MCP Hangar requires at least one of the following to run providers:\n")

    instructions = get_install_instructions(["npx", "uvx", "docker/podman"])
    for runtime, instruction in instructions.items():
        console.print(f"  [bold]{runtime}[/bold]: {instruction}")

    console.print("\n[dim]Install one of the above and run 'mcp-hangar init' again.[/dim]")
    raise typer.Exit(1)


def _prompt_provider_selection(deps: DependencyStatus) -> list[str]:
    """Interactive provider selection with categories."""
    available_cats, unavailable_cats = get_providers_by_category_filtered(deps)
    selected = []

    console.print("\n[bold]Select providers to enable:[/bold]")
    console.print("[dim]Use arrow keys and space to select, Enter to confirm[/dim]\n")

    for category, providers in available_cats.items():
        is_starter = category == "Starter"
        choices = [
            questionary.Choice(
                title=f"{p.name} - {p.description}",
                value=p.name,
                checked=is_starter,
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

    if unavailable_cats:
        console.print("\n[dim]Unavailable providers (missing dependencies):[/dim]")
        for category, providers in unavailable_cats.items():
            for p in providers:
                reason = p.get_unavailable_reason(deps)
                console.print(f"  [dim]{p.name} - {p.description} ({reason})[/dim]")

    return selected


def _collect_provider_config(provider: ProviderDefinition) -> dict | None:
    """Collect configuration for a provider that requires it."""
    if not provider.requires_config:
        return {}

    if provider.env_var and os.environ.get(provider.env_var):
        use_env = questionary.confirm(
            f"{provider.name}: Use existing ${provider.env_var} environment variable?",
            default=True,
        ).ask()

        if use_env:
            return {"use_env": provider.env_var}

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


def _prompt_existing_config_action(
    config_mgr: ConfigFileManager,
    selected_providers: list[str],
) -> str:
    """Prompt user for action when config already exists.

    Args:
        config_mgr: ConfigFileManager instance.
        selected_providers: List of provider names to be configured.

    Returns:
        One of ExistingConfigAction values.
    """
    existing_providers = config_mgr.list_providers()

    console.print(f"  [yellow]Configuration exists at {config_mgr.config_path}[/yellow]")
    console.print(f"  [dim]Existing providers: {', '.join(existing_providers) or '(none)'}[/dim]")
    console.print(f"  [dim]New providers: {', '.join(selected_providers) or '(none)'}[/dim]")

    # Check for overlapping providers
    overlap = set(existing_providers) & set(selected_providers)
    if overlap:
        console.print(f"  [dim]Overlapping (will be skipped in merge): {', '.join(overlap)}[/dim]")

    choices = [
        questionary.Choice(
            title="Merge - Add new providers, keep existing ones",
            value=ExistingConfigAction.MERGE,
        ),
        questionary.Choice(
            title="Backup & Overwrite - Save backup, then replace with new config",
            value=ExistingConfigAction.BACKUP_OVERWRITE,
        ),
        questionary.Choice(
            title="Abort - Cancel and keep existing config",
            value=ExistingConfigAction.ABORT,
        ),
    ]

    action = questionary.select(
        "Configuration already exists. What would you like to do?",
        choices=choices,
    ).ask()

    if action is None:
        raise typer.Abort()

    return action


def _show_completion_summary(
    providers: list[str],
    hangar_config_path: Path,
    claude_config_path: Path | None,
    backup_path: Path | None,
    smoke_test_passed: bool = True,
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

    if providers:
        if smoke_test_passed:
            table.add_row("Provider tests", "[green]All passed[/green]")
        else:
            table.add_row("Provider tests", "[yellow]Some failed[/yellow]")

    title = (
        "[bold green]Setup Complete[/bold green]"
        if smoke_test_passed
        else "[bold yellow]Setup Complete (with warnings)[/bold yellow]"
    )
    border = "green" if smoke_test_passed else "yellow"

    console.print(Panel(table, title=title, border_style=border))

    if providers:
        console.print("\n[bold]Enabled providers:[/bold]")
        for name in providers:
            console.print(f"  [green]+[/green] {name}")

    console.print("\n[bold]Next steps:[/bold]")
    if not smoke_test_passed:
        console.print("  1. [bold]Review errors above[/bold] and fix provider configuration")
        console.print("  2. Run [bold]mcp-hangar serve[/bold] to test manually")
        console.print("  3. [bold]Restart Claude Desktop[/bold] when ready")
    else:
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
    skip_test: Annotated[
        bool,
        typer.Option("--skip-test", help="Skip smoke test after configuration"),
    ] = False,
    reset: Annotated[
        bool,
        typer.Option("--reset", help="Reset existing configuration"),
    ] = False,
):
    """Initialize MCP Hangar with interactive setup wizard.

    This wizard will:
    - Detect available runtimes (npx, uvx, docker)
    - Detect your Claude Desktop installation
    - Help you select which MCP providers to enable
    - Create a configuration file
    - Test providers to verify configuration
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

    # Step 0: Detect available runtimes
    deps = detect_dependencies()

    console.print("\n[bold]Step 0:[/bold] Detecting available runtimes...")
    if non_interactive:
        if deps.available_runtimes:
            console.print(f"  [green]Available:[/green] {', '.join(deps.available_runtimes)}")
        if deps.missing_runtimes:
            console.print(f"  [dim]Not found: {', '.join(deps.missing_runtimes)}[/dim]")
    else:
        _show_dependency_status(deps)

    _check_dependencies_or_exit(deps, non_interactive)

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
        requested = [p.strip() for p in providers_opt.split(",")]
        available = []
        unavailable = []

        for name in requested:
            provider = get_provider(name)
            if provider is None:
                console.print(f"  [yellow]Unknown provider: {name}[/yellow]")
            elif not provider.is_available(deps):
                reason = provider.get_unavailable_reason(deps)
                console.print(f"  [yellow]Skipping {name} ({reason})[/yellow]")
                unavailable.append(name)
            else:
                available.append(name)

        selected_providers = available
        if available:
            console.print(f"  Using providers: {', '.join(available)}")
        if unavailable:
            console.print(f"  [dim]Unavailable: {', '.join(unavailable)}[/dim]")

    elif bundle:
        if bundle.lower() not in PROVIDER_BUNDLES:
            raise CLIError(
                message=f"Unknown bundle: {bundle}",
                reason=f"Available bundles: {', '.join(PROVIDER_BUNDLES.keys())}",
                suggestions=["Use --bundle=starter, --bundle=developer, or --bundle=data"],
            )

        available, unavailable = filter_bundle_by_availability(bundle.lower(), deps)
        selected_providers = available

        if available:
            console.print(f"  Using '{bundle}' bundle: {', '.join(available)}")
        if unavailable:
            console.print(f"  [yellow]Skipping from bundle ({', '.join(unavailable)}) - missing dependencies[/yellow]")

    elif non_interactive:
        available, unavailable = filter_bundle_by_availability("starter", deps)
        selected_providers = available

        if available:
            console.print(f"  Using default providers: {', '.join(available)}")
        if unavailable:
            console.print(f"  [yellow]Skipping ({', '.join(unavailable)}) - missing dependencies[/yellow]")

    else:
        selected_providers = _prompt_provider_selection(deps)

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

    backup_path = None
    merged_providers = False
    final_providers = selected_providers  # Track what ends up in config

    if config_mgr.exists() and not reset:
        if non_interactive:
            # Non-interactive mode: always backup and overwrite
            backup_path = config_mgr.backup()
            if backup_path:
                console.print(f"  [dim]Backed up existing config to: {backup_path}[/dim]")
            action = ExistingConfigAction.BACKUP_OVERWRITE
        else:
            # Interactive mode: prompt for action
            action = _prompt_existing_config_action(config_mgr, selected_providers)

        if action == ExistingConfigAction.ABORT:
            console.print("  [yellow]Aborted - existing configuration preserved[/yellow]")
            raise typer.Abort()

        elif action == ExistingConfigAction.MERGE:
            # Merge new providers with existing
            provider_defs = [get_provider(name) for name in selected_providers]
            provider_defs = [p for p in provider_defs if p is not None]

            try:
                added, skipped, total = config_mgr.merge_providers(provider_defs, provider_configs, deps)

                if added:
                    console.print(f"  [green]Added:[/green] {', '.join(added)}")
                if skipped:
                    console.print(f"  [dim]Skipped (already exist): {', '.join(skipped)}[/dim]")
                console.print(f"  [green]Updated:[/green] {config_mgr.config_path}")
                console.print(f"  [dim]Total providers: {len(total)}[/dim]")

                final_providers = total
                merged_providers = True

            except OSError as e:
                raise PermissionError(str(config_mgr.config_path), "write") from e

        elif action == ExistingConfigAction.BACKUP_OVERWRITE:
            # Backup and overwrite
            if not backup_path:  # Wasn't backed up in non-interactive mode
                backup_path = config_mgr.backup()
                if backup_path:
                    console.print(f"  [dim]Backed up to: {backup_path}[/dim]")

            provider_defs = [get_provider(name) for name in selected_providers]
            provider_defs = [p for p in provider_defs if p is not None]

            try:
                config_mgr.write_initial_config(provider_defs, provider_configs, deps)
                console.print(f"  [green]Created:[/green] {config_mgr.config_path}")
            except OSError as e:
                raise PermissionError(str(config_mgr.config_path), "write") from e

    else:
        # No existing config or reset flag - write fresh config
        provider_defs = [get_provider(name) for name in selected_providers]
        provider_defs = [p for p in provider_defs if p is not None]

        try:
            config_mgr.write_initial_config(provider_defs, provider_configs, deps)
            console.print(f"  [green]Created:[/green] {config_mgr.config_path}")
        except OSError as e:
            raise PermissionError(str(config_mgr.config_path), "write") from e

    # Step 5: Smoke test providers
    smoke_test_passed = True
    providers_to_test = final_providers if merged_providers else selected_providers
    if providers_to_test and not skip_test:
        console.print("\n[bold]Step 5:[/bold] Testing providers...")
        console.print("  [dim]Starting each provider to verify configuration (max 10s)[/dim]\n")

        try:
            test_result = run_smoke_test(
                config_path=config_mgr.config_path,
                timeout_s=10.0,
                console=console,
            )

            if test_result.all_passed:
                console.print(
                    f"\n  [green]All {test_result.passed_count} providers ready[/green] "
                    f"({test_result.total_duration_ms:.0f}ms)"
                )
            else:
                smoke_test_passed = False
                console.print(
                    f"\n  [yellow]{test_result.failed_count} of {len(test_result.results)} "
                    f"providers failed[/yellow]"
                )
                console.print("  [dim]Configuration saved - fix issues and run 'mcp-hangar status'[/dim]")

        except Exception as e:
            smoke_test_passed = False
            console.print(f"  [yellow]Smoke test failed: {e}[/yellow]")
            console.print("  [dim]Configuration saved - verify manually with 'mcp-hangar serve'[/dim]")

    # Step 6: Update Claude Desktop config
    claude_backup_path = None
    if not skip_claude and claude_mgr.exists():
        console.print("\n[bold]Step 6:[/bold] Updating Claude Desktop...")

        claude_backup_path = claude_mgr.backup()
        if claude_backup_path:
            console.print(f"  [dim]Backed up to: {claude_backup_path}[/dim]")

        try:
            claude_mgr.update_for_hangar(config_mgr.config_path)
            console.print(f"  [green]Updated:[/green] {claude_mgr.config_path}")
        except OSError as e:
            raise PermissionError(str(claude_mgr.config_path), "write") from e

    # Step 7: Completion summary
    _show_completion_summary(
        providers=selected_providers,
        hangar_config_path=config_mgr.config_path,
        claude_config_path=claude_mgr.config_path if not skip_claude else None,
        backup_path=claude_backup_path or backup_path,
        smoke_test_passed=smoke_test_passed,
    )


__all__ = ["app", "init_command"]
