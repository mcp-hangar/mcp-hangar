"""Init command - Interactive setup wizard for MCP Hangar.

This command provides the "5-minute experience" for new users:
1. Detect available runtimes (npx, uvx, docker)
2. Detect the MCP clients on this machine (Claude Code, Cursor, Claude Desktop)
3. Present MCP server selection with bundles (filtered by available deps)
4. Collect required configuration
5. Generate the MCP Hangar config file
6. Smoke test every server -- and take a digest pin for each tool while it is up
7. Point the selected clients at Hangar, and show what was written

The configuration this writes governs. It used to configure a fleet and enforce
nothing -- no `tool_access`, no pins, no identity -- so a first run ended every
call in `allow` while the project's own pitch is that a call ends in a verdict
(#1192).
"""

import os
from pathlib import Path
from typing import Annotated, cast

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from ..errors import CLIError, PermissionError
from ..main import GlobalOptions
from ..services import (
    ConfigFileManager,
    DependencyStatus,
    detect_dependencies,
    filter_bundle_by_availability,
    get_install_instructions,
    get_mcp_server,
    get_mcp_servers_by_category_filtered,
    PROVIDER_BUNDLES,
    McpServerDefinition,
    run_smoke_test,
)
from ..services.mcp_clients import McpClient, client_by_key, detect_clients, write_hangar_entry


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
    console.print("MCP Hangar requires at least one of the following to run mcp_servers:\n")

    instructions = get_install_instructions(["npx", "uvx", "docker/podman"])
    for runtime, instruction in instructions.items():
        console.print(f"  [bold]{runtime}[/bold]: {instruction}")

    console.print("\n[dim]Install one of the above and run 'mcp-hangar init' again.[/dim]")
    raise typer.Exit(1)


def _prompt_mcp_server_selection(deps: DependencyStatus) -> list[str]:
    """Interactive MCP server selection with categories."""
    available_cats, unavailable_cats = get_mcp_servers_by_category_filtered(deps)
    selected = []

    console.print("\n[bold]Select MCP servers to enable:[/bold]")
    console.print("[dim]Use arrow keys and space to select, Enter to confirm[/dim]\n")

    for category, mcp_servers in available_cats.items():
        is_starter = category == "Starter"
        choices = [
            questionary.Choice(
                title=f"{p.name} - {p.description}",
                value=p.name,
                checked=is_starter,
            )
            for p in mcp_servers
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
        console.print("\n[dim]Unavailable MCP servers (missing dependencies):[/dim]")
        for category, mcp_servers in unavailable_cats.items():
            for p in mcp_servers:
                reason = p.get_unavailable_reason(deps)
                console.print(f"  [dim]{p.name} - {p.description} ({reason})[/dim]")

    return selected


def _collect_mcp_server_config(mcp_server: McpServerDefinition) -> dict | None:
    """Collect configuration for an MCP server that requires it."""
    if not mcp_server.requires_config:
        return {}

    if mcp_server.env_var and os.environ.get(mcp_server.env_var):
        use_env = questionary.confirm(
            f"{mcp_server.name}: Use existing ${mcp_server.env_var} environment variable?",
            default=True,
        ).ask()

        if use_env:
            return {"use_env": mcp_server.env_var}

    if mcp_server.config_type == "secret":
        console.print(f"\n[dim]For {mcp_server.name}: {mcp_server.config_prompt}[/dim]")
        if mcp_server.env_var:
            console.print(f"[dim]Tip: You can also set ${mcp_server.env_var} in your shell profile[/dim]")

        value = questionary.password(
            f"{mcp_server.config_prompt} (or press Enter to skip):",
        ).ask()

        if not value:
            msg = f"Skipping {mcp_server.name} - configure later with 'mcp-hangar configure {mcp_server.name}'"
            console.print(f"[yellow]{msg}[/yellow]")
            return None

        return {"value": value, "env_var": mcp_server.env_var}

    elif mcp_server.config_type == "path":
        default_path = str(Path.home())
        value = questionary.path(
            f"{mcp_server.config_prompt}:",
            default=default_path,
            only_directories=True,
        ).ask()

        if not value:
            return None

        return {"path": str(Path(value).expanduser().resolve())}

    else:
        value = questionary.text(f"{mcp_server.config_prompt}:").ask()
        return {"value": value} if value else None


def _prompt_existing_config_action(
    config_mgr: ConfigFileManager,
    selected_mcp_servers: list[str],
) -> str:
    """Prompt user for action when config already exists.

    Args:
        config_mgr: ConfigFileManager instance.
        selected_mcp_servers: List of mcp_server names to be configured.

    Returns:
        One of ExistingConfigAction values.
    """
    existing_mcp_servers = config_mgr.list_mcp_servers()

    console.print(f"  [yellow]Configuration exists at {config_mgr.config_path}[/yellow]")
    console.print(f"  [dim]Existing mcp_servers: {', '.join(existing_mcp_servers) or '(none)'}[/dim]")
    console.print(f"  [dim]New mcp_servers: {', '.join(selected_mcp_servers) or '(none)'}[/dim]")

    # Check for overlapping mcp_servers
    overlap = set(existing_mcp_servers) & set(selected_mcp_servers)
    if overlap:
        console.print(f"  [dim]Overlapping (will be skipped in merge): {', '.join(overlap)}[/dim]")

    choices = [
        questionary.Choice(
            title="Merge - Add new MCP servers, keep existing ones",
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

    return cast(str, action)


def _show_completion_summary(
    mcp_servers: list[str],
    hangar_config_path: Path,
    client_paths: list[Path],
    backup_path: Path | None,
    smoke_test_status: str,
    pinned_tools: int,
):
    """Display completion summary with next steps.

    `smoke_test_status` is a state, not a flag. It used to default to `True`,
    so `--skip-test` -- which runs no test at all -- printed "All passed"
    (#1192). A panel that reports a pass nobody measured is worse than one that
    says nothing.
    """
    console.print()
    passed = smoke_test_status == "passed"
    skipped = smoke_test_status == "skipped"

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    table.add_column("Item", style="bold")
    table.add_column("Value")

    table.add_row("MCP servers configured", str(len(mcp_servers)))
    table.add_row("MCP Hangar config", str(hangar_config_path))
    for path in client_paths:
        table.add_row("Client config", str(path))
    if backup_path:
        table.add_row("Backup created", str(backup_path))

    if mcp_servers:
        table.add_row(
            "MCP server tests",
            {
                "passed": "[green]All passed[/green]",
                "failed": "[yellow]Some failed[/yellow]",
                "skipped": "[dim]Not run (--skip-test)[/dim]",
            }[smoke_test_status],
        )
        table.add_row(
            "Digest pins",
            f"[green]{pinned_tools} tool(s) pinned[/green]"
            if pinned_tools
            else "[dim]None -- pins are taken during the test[/dim]",
        )

    title = (
        "[bold green]Setup Complete[/bold green]"
        if passed
        else "[bold yellow]Setup Complete (with warnings)[/bold yellow]"
    )
    border = "green" if passed else "yellow"

    console.print(Panel(table, title=title, border_style=border))

    if mcp_servers:
        console.print("\n[bold]Enabled MCP servers:[/bold]")
        for name in mcp_servers:
            console.print(f"  [green]+[/green] {name}")

    console.print("\n[bold]Next steps:[/bold]")
    if smoke_test_status == "failed":
        console.print("  1. [bold]Review errors above[/bold] and fix the server configuration")
        console.print("  2. Run [bold]mcp-hangar serve[/bold] to test manually")
        console.print("  3. [bold]Restart your MCP client[/bold] when ready")
    elif skipped:
        console.print("  1. Run [bold]mcp-hangar pin --write[/bold] to pin what your servers serve")
        console.print("  2. [bold]Restart your MCP client[/bold] to activate the new configuration")
        console.print("  3. Run [bold]mcp-hangar status[/bold] to verify the servers are healthy")
    else:
        console.print("  1. [bold]Restart your MCP client[/bold] to activate the new configuration")
        console.print("  2. Run [bold]mcp-hangar pin --check[/bold] any time to see whether a tool changed")
        console.print("  3. Run [bold]mcp-hangar add <server>[/bold] to add more servers later")
    console.print("\n[dim]Need help? Visit https://mcp-hangar.io/docs[/dim]")


def _resolve_clients(
    client_keys: list[str] | None,
    claude_config_path: Path | None,
    skip_clients: bool,
    non_interactive: bool,
) -> list[McpClient]:
    """Which client config files this run should write.

    Named clients win; otherwise what exists on the machine is used, and an
    interactive run with more than one is asked rather than guessed at.
    """
    if skip_clients:
        return []

    if claude_config_path is not None:
        return [McpClient("custom", "Custom client config", claude_config_path)]

    if client_keys:
        if "all" in client_keys:
            return detect_clients() or []
        chosen = []
        for key in client_keys:
            client = client_by_key(key)
            if client is None:
                raise CLIError(
                    f"Unknown client '{key}'. Known: claude-code, claude-code-project, "
                    "cursor, cursor-project, claude-desktop, all."
                )
            chosen.append(client)
        return chosen

    detected = detect_clients()
    if len(detected) <= 1 or non_interactive:
        return detected

    picked = questionary.checkbox(
        "Which clients should point at Hangar?",
        choices=[questionary.Choice(f"{c.label} -- {c.path}", value=c.key, checked=True) for c in detected],
    ).ask()
    if picked is None:
        raise typer.Abort()
    return [c for c in detected if c.key in picked]


@app.callback(invoke_without_command=True)
def init_command(  # noqa: C901 -- baseline CC=49; split before extending
    ctx: typer.Context,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", "-y", help="Run without prompts, using defaults"),
    ] = False,
    bundle: Annotated[
        str | None,
        typer.Option("--bundle", "-b", help="MCP server bundle to install: starter, developer, data"),
    ] = None,
    mcp_servers_opt: Annotated[
        str | None,
        typer.Option(
            "--servers",
            # The old spelling, kept because it is in scripts and in the docs
            # people already have. It is the identifier leaking into the flag
            # surface, which is the thing #1195 is about, so the new name leads.
            "--mcp_servers",
            help="Comma-separated list of MCP servers to install",
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config-path", help="Custom path for MCP Hangar config file"),
    ] = None,
    claude_config_path: Annotated[
        Path | None,
        typer.Option("--claude-config", help="Custom path to a client config file to write"),
    ] = None,
    client_keys: Annotated[
        list[str] | None,
        typer.Option(
            "--client",
            help="Client to point at Hangar: claude-code, claude-code-project, cursor, "
            "cursor-project, claude-desktop, or all. Repeatable; default is what is detected.",
        ),
    ] = None,
    skip_clients: Annotated[
        bool,
        typer.Option("--skip-clients", "--skip-claude", help="Do not modify any MCP client config"),
    ] = False,
    skip_test: Annotated[
        bool,
        typer.Option(
            "--skip-test",
            help="Skip the smoke test. No digest pins are taken either -- they come from that run.",
        ),
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
    - Help you select which MCP servers to enable
    - Create a configuration file
    - Test MCP servers to verify configuration
    - Update Claude Desktop to use MCP Hangar

    Examples:
        mcp-hangar init
        mcp-hangar init --bundle starter
        mcp-hangar init --MCP servers filesystem,github,sqlite
        mcp-hangar init --non-interactive --bundle developer
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()

    # Initialize managers
    effective_config_path = config_path or global_opts.config or ConfigFileManager.DEFAULT_CONFIG_PATH
    config_mgr = ConfigFileManager(effective_config_path)

    # Step 1: Detect available runtimes
    deps = detect_dependencies()

    console.print("\n[bold]Step 1:[/bold] Detecting available runtimes...")
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
                "Your client connects to one process, and every tool call it makes\n"
                "goes through a policy you can read in one file.",
                title="MCP Hangar Setup",
                border_style="blue",
            )
        )

    # Step 2: Detect the MCP clients on this machine
    console.print("\n[bold]Step 2:[/bold] Detecting MCP clients...")

    clients = _resolve_clients(
        client_keys=client_keys,
        claude_config_path=claude_config_path,
        skip_clients=skip_clients,
        non_interactive=non_interactive,
    )
    if clients:
        for client in clients:
            console.print(f"  [green]Found:[/green] {client.label} -- {client.path}")
    elif not skip_clients:
        console.print("  [yellow]No MCP client config found - you can point one at Hangar yourself[/yellow]")

    # Step 3: McpServer selection
    console.print("\n[bold]Step 3:[/bold] Selecting MCP servers...")

    selected_mcp_servers: list[str] = []
    mcp_server_configs: dict[str, dict] = {}

    if mcp_servers_opt:
        requested = [p.strip() for p in mcp_servers_opt.split(",")]
        available = []
        unavailable = []

        for name in requested:
            mcp_server = get_mcp_server(name)
            if mcp_server is None:
                console.print(f"  [yellow]Unknown MCP server: {name}[/yellow]")
            elif not mcp_server.is_available(deps):
                reason = mcp_server.get_unavailable_reason(deps)
                console.print(f"  [yellow]Skipping {name} ({reason})[/yellow]")
                unavailable.append(name)
            else:
                available.append(name)

        selected_mcp_servers = available
        if available:
            console.print(f"  Using mcp_servers: {', '.join(available)}")
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
        selected_mcp_servers = available

        if available:
            console.print(f"  Using '{bundle}' bundle: {', '.join(available)}")
        if unavailable:
            console.print(f"  [yellow]Skipping from bundle ({', '.join(unavailable)}) - missing dependencies[/yellow]")

    elif non_interactive:
        available, unavailable = filter_bundle_by_availability("starter", deps)
        selected_mcp_servers = available

        if available:
            console.print(f"  Using default mcp_servers: {', '.join(available)}")
        if unavailable:
            console.print(f"  [yellow]Skipping ({', '.join(unavailable)}) - missing dependencies[/yellow]")

    else:
        selected_mcp_servers = _prompt_mcp_server_selection(deps)

    if not selected_mcp_servers:
        console.print("  [yellow]No MCP servers selected[/yellow]")
        if not non_interactive:
            proceed = questionary.confirm("Continue with empty configuration?", default=False).ask()
            if not proceed:
                raise typer.Abort()

    # Step 4: Collect mcp_server configurations
    console.print("\n[bold]Step 4:[/bold] Configuring MCP servers...")
    if selected_mcp_servers and not non_interactive:
        for name in list(selected_mcp_servers):
            mcp_server = get_mcp_server(name)
            if mcp_server and mcp_server.requires_config:
                config = _collect_mcp_server_config(mcp_server)
                if config is None:
                    selected_mcp_servers.remove(name)
                else:
                    mcp_server_configs[name] = config

    # Step 5: Generate configuration files
    console.print("\n[bold]Step 5:[/bold] Generating configuration...")

    backup_path = None
    merged_mcp_servers = False
    final_mcp_servers = selected_mcp_servers  # Track what ends up in config

    if config_mgr.exists() and not reset:
        if non_interactive:
            # Non-interactive mode: always backup and overwrite
            backup_path = config_mgr.backup()
            if backup_path:
                console.print(f"  [dim]Backed up existing config to: {backup_path}[/dim]")
            action = ExistingConfigAction.BACKUP_OVERWRITE
        else:
            # Interactive mode: prompt for action
            action = _prompt_existing_config_action(config_mgr, selected_mcp_servers)

        if action == ExistingConfigAction.ABORT:
            console.print("  [yellow]Aborted - existing configuration preserved[/yellow]")
            raise typer.Abort()

        elif action == ExistingConfigAction.MERGE:
            # Merge new mcp_servers with existing
            mcp_server_defs_raw = [get_mcp_server(name) for name in selected_mcp_servers]
            mcp_server_defs = [p for p in mcp_server_defs_raw if p is not None]

            try:
                added, skipped, total = config_mgr.merge_mcp_servers(mcp_server_defs, mcp_server_configs, deps)

                if added:
                    console.print(f"  [green]Added:[/green] {', '.join(added)}")
                if skipped:
                    console.print(f"  [dim]Skipped (already exist): {', '.join(skipped)}[/dim]")
                console.print(f"  [green]Updated:[/green] {config_mgr.config_path}")
                console.print(f"  [dim]Total mcp_servers: {len(total)}[/dim]")

                final_mcp_servers = total
                merged_mcp_servers = True

            except OSError as e:
                raise PermissionError(str(config_mgr.config_path), "write") from e

        elif action == ExistingConfigAction.BACKUP_OVERWRITE:
            # Backup and overwrite
            if not backup_path:  # Wasn't backed up in non-interactive mode
                backup_path = config_mgr.backup()
                if backup_path:
                    console.print(f"  [dim]Backed up to: {backup_path}[/dim]")

            mcp_server_defs_raw = [get_mcp_server(name) for name in selected_mcp_servers]
            mcp_server_defs = [p for p in mcp_server_defs_raw if p is not None]

            try:
                config_mgr.write_initial_config(mcp_server_defs, mcp_server_configs, deps)
                console.print(f"  [green]Created:[/green] {config_mgr.config_path}")
            except OSError as e:
                raise PermissionError(str(config_mgr.config_path), "write") from e

    else:
        # No existing config or reset flag - write fresh config
        mcp_server_defs_raw = [get_mcp_server(name) for name in selected_mcp_servers]
        mcp_server_defs = [p for p in mcp_server_defs_raw if p is not None]

        try:
            config_mgr.write_initial_config(mcp_server_defs, mcp_server_configs, deps)
            console.print(f"  [green]Created:[/green] {config_mgr.config_path}")
        except OSError as e:
            raise PermissionError(str(config_mgr.config_path), "write") from e

    # Step 6: Smoke test the servers, and pin what they serve while they are up
    smoke_test_status = "skipped"
    pins: dict[str, dict[str, str]] = {}
    mcp_servers_to_test = final_mcp_servers if merged_mcp_servers else selected_mcp_servers
    if mcp_servers_to_test and not skip_test:
        console.print("\n[bold]Step 6:[/bold] Testing MCP servers...")
        console.print("  [dim]Starting each server to verify configuration and pin its tools[/dim]\n")

        try:
            test_result = run_smoke_test(
                config_path=config_mgr.config_path,
                console=console,
            )
            pins = {r.mcp_server_id: r.digests for r in test_result.results if r.success and r.digests}

            if test_result.all_passed:
                smoke_test_status = "passed"
                console.print(
                    f"\n  [green]All {test_result.passed_count} MCP servers ready[/green] "
                    f"({test_result.total_duration_ms:.0f}ms)"
                )
            else:
                smoke_test_status = "failed"
                console.print(
                    f"\n  [yellow]{test_result.failed_count} of {len(test_result.results)} MCP servers failed[/yellow]"
                )
                console.print("  [dim]Configuration saved - fix issues and run 'mcp-hangar status'[/dim]")

        except Exception as e:  # noqa: BLE001 -- fault-barrier: smoke test failure must not crash init wizard
            smoke_test_status = "failed"
            console.print(f"  [yellow]Smoke test failed: {e}[/yellow]")
            console.print("  [dim]Configuration saved - verify manually with 'mcp-hangar serve'[/dim]")

    # The pins are only writable into a file this command owns. After a MERGE the
    # file is the user's, with servers this run never started, and rewriting it
    # from the wizard's inputs would drop them.
    pinned_tools = 0
    if pins and not merged_mcp_servers:
        try:
            config_mgr.write_initial_config(mcp_server_defs, mcp_server_configs, deps, pins=pins)
            pinned_tools = sum(len(tools) for tools in pins.values())
            console.print(f"  [green]Pinned:[/green] {pinned_tools} tool(s) in {config_mgr.config_path}")
        except OSError as e:
            raise PermissionError(str(config_mgr.config_path), "write") from e
    elif pins and merged_mcp_servers:
        console.print("  [dim]Existing config merged - run 'mcp-hangar pin --write' to pin its tools[/dim]")

    # Step 7: Point the selected clients at Hangar
    client_backup_path = None
    written_clients: list[McpClient] = []
    if not skip_clients and clients:
        console.print("\n[bold]Step 7:[/bold] Updating MCP clients...")

        for client in clients:
            try:
                backup = write_hangar_entry(client, config_mgr.config_path)
            except OSError as e:
                raise PermissionError(str(client.path), "write") from e
            except ValueError as e:
                # An unparseable client file is not ours to rewrite.
                console.print(f"  [yellow]Skipped {client.label}: {e}[/yellow]")
                continue
            written_clients.append(client)
            client_backup_path = backup or client_backup_path
            console.print(f"  [green]Updated:[/green] {client.path}")
            if backup:
                console.print(f"  [dim]Backed up to: {backup}[/dim]")

    _show_completion_summary(
        mcp_servers=selected_mcp_servers,
        hangar_config_path=config_mgr.config_path,
        client_paths=[client.path for client in written_clients],
        backup_path=client_backup_path or backup_path,
        smoke_test_status=smoke_test_status,
        pinned_tools=pinned_tools,
    )


__all__ = ["app", "init_command"]
