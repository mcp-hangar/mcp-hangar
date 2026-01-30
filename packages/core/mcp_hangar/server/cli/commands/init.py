"""Init command - Interactive setup wizard for MCP Hangar.

This command provides the "5-minute experience" for new users:
1. Detect Claude Desktop installation
2. Present provider selection with bundles
3. Collect required configuration
4. Generate MCP Hangar config file
5. Update Claude Desktop config
6. Show completion summary with next steps
"""

from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
from typing import Annotated

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from ..errors import CLIError, PermissionError
from ..main import GlobalOptions

app = typer.Typer(
    name="init",
    help="Initialize MCP Hangar with interactive setup wizard",
    invoke_without_command=True,
)

console = Console()


# Platform-specific paths for Claude Desktop config
CLAUDE_DESKTOP_PATHS = {
    "Darwin": Path.home() / "Library" / "Application Support" / "Claude",
    "Linux": Path.home() / ".config" / "claude",
    "Windows": Path(os.environ.get("APPDATA", "")) / "Claude",
}

# Default MCP Hangar config location
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "mcp-hangar"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"


def _detect_claude_desktop() -> tuple[Path | None, dict | None]:
    """Detect Claude Desktop installation and load existing config.

    Returns:
        Tuple of (config_path, existing_config) or (None, None) if not found.
    """
    system = platform.system()
    search_paths = []

    # Add platform-specific path
    if system in CLAUDE_DESKTOP_PATHS:
        search_paths.append(CLAUDE_DESKTOP_PATHS[system])

    # Add common fallback paths
    search_paths.extend(
        [
            Path.home() / ".claude",
            Path.home() / ".config" / "claude",
        ]
    )

    for base_path in search_paths:
        config_file = base_path / "claude_desktop_config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                return config_file, config
            except (json.JSONDecodeError, OSError):
                # Invalid config, try next path
                continue

    return None, None


def _get_provider_categories() -> dict[str, list[dict]]:
    """Get available provider categories with descriptions.

    Returns:
        Dictionary mapping category names to lists of provider definitions.
    """
    return {
        "Starter (recommended for everyone)": [
            {
                "name": "filesystem",
                "description": "Read and write local files",
                "package": "@anthropic/mcp-server-filesystem",
                "requires_config": True,
                "config_prompt": "Directory to allow access to",
                "config_key": "args",
                "config_type": "path",
            },
            {
                "name": "fetch",
                "description": "Make HTTP requests to fetch web content",
                "package": "@anthropic/mcp-server-fetch",
                "requires_config": False,
            },
            {
                "name": "memory",
                "description": "Persistent key-value storage for context",
                "package": "@anthropic/mcp-server-memory",
                "requires_config": False,
            },
        ],
        "Developer Tools": [
            {
                "name": "github",
                "description": "GitHub repos, issues, PRs",
                "package": "@anthropic/mcp-server-github",
                "requires_config": True,
                "config_prompt": "GitHub personal access token",
                "config_key": "env.GITHUB_TOKEN",
                "config_type": "secret",
                "env_var": "GITHUB_TOKEN",
            },
            {
                "name": "git",
                "description": "Local git operations",
                "package": "@anthropic/mcp-server-git",
                "requires_config": False,
            },
        ],
        "Data & Databases": [
            {
                "name": "sqlite",
                "description": "Query SQLite databases",
                "package": "@anthropic/mcp-server-sqlite",
                "requires_config": True,
                "config_prompt": "Path to SQLite database file",
                "config_key": "args",
                "config_type": "path",
            },
            {
                "name": "postgres",
                "description": "Query PostgreSQL databases",
                "package": "@anthropic/mcp-server-postgres",
                "requires_config": True,
                "config_prompt": "PostgreSQL connection string",
                "config_key": "env.DATABASE_URL",
                "config_type": "secret",
                "env_var": "DATABASE_URL",
            },
        ],
    }


def _prompt_provider_selection() -> list[str]:
    """Interactive provider selection with categories.

    Returns:
        List of selected provider names.
    """
    categories = _get_provider_categories()
    selected = []

    console.print("\n[bold]Select providers to enable:[/bold]")
    console.print("[dim]Use arrow keys and space to select, Enter to confirm[/dim]\n")

    for category, providers in categories.items():
        choices = [
            questionary.Choice(
                title=f"{p['name']} - {p['description']}",
                value=p["name"],
                checked=category.startswith("Starter"),  # Pre-select starter providers
            )
            for p in providers
        ]

        if choices:
            category_selected = questionary.checkbox(
                category,
                choices=choices,
            ).ask()

            if category_selected is None:
                # User cancelled
                raise typer.Abort()

            selected.extend(category_selected)

    return selected


def _collect_provider_config(provider_name: str, provider_def: dict) -> dict | None:
    """Collect configuration for a provider that requires it.

    Args:
        provider_name: Name of the provider
        provider_def: Provider definition dictionary

    Returns:
        Configuration dictionary or None if skipped
    """
    if not provider_def.get("requires_config"):
        return {}

    config_type = provider_def.get("config_type", "string")
    config_prompt = provider_def.get("config_prompt", "Configuration value")
    env_var = provider_def.get("env_var")

    # Check if env var is already set
    if env_var and os.environ.get(env_var):
        use_env = questionary.confirm(
            f"{provider_name}: Use existing ${env_var} environment variable?",
            default=True,
        ).ask()

        if use_env:
            return {"use_env": env_var}

    # Prompt based on config type
    if config_type == "secret":
        console.print(f"\n[dim]For {provider_name}: {config_prompt}[/dim]")
        if env_var:
            console.print(f"[dim]Tip: You can also set ${env_var} in your shell profile[/dim]")

        value = questionary.password(
            f"{config_prompt} (or press Enter to skip):",
        ).ask()

        if not value:
            msg = f"Skipping {provider_name} - configure later with 'mcp-hangar configure {provider_name}'"
            console.print(f"[yellow]{msg}[/yellow]")
            return None

        return {"value": value, "env_var": env_var}

    elif config_type == "path":
        default_path = str(Path.home())
        value = questionary.path(
            f"{config_prompt}:",
            default=default_path,
            only_directories=True,
        ).ask()

        if not value:
            return None

        # Expand user path
        return {"path": str(Path(value).expanduser().resolve())}

    else:
        value = questionary.text(
            f"{config_prompt}:",
        ).ask()

        if not value:
            return None

        return {"value": value}


def _generate_hangar_config(providers: list[str], configs: dict[str, dict]) -> str:
    """Generate MCP Hangar config.yaml content.

    Args:
        providers: List of enabled provider names
        configs: Dictionary mapping provider names to their configurations

    Returns:
        YAML configuration string
    """
    lines = [
        "# MCP Hangar Configuration",
        "# Generated by 'mcp-hangar init'",
        "#",
        "# Documentation: https://docs.mcp-hangar.io/configuration",
        "",
        "providers:",
    ]

    categories = _get_provider_categories()
    provider_defs = {}
    for cat_providers in categories.values():
        for p in cat_providers:
            provider_defs[p["name"]] = p

    for name in providers:
        provider_def = provider_defs.get(name, {})
        config = configs.get(name, {})
        package = provider_def.get("package", f"@anthropic/mcp-server-{name}")

        lines.append(f"  {name}:")
        lines.append("    mode: subprocess")
        # Quote package name because @ is a special character in YAML
        lines.append(f'    command: [npx, -y, "{package}"]')

        # Add args if path-based config
        if config and config.get("path"):
            lines.append(f'    args: ["{config["path"]}"]')

        # Add environment variables
        if config:
            env_var = config.get("env_var")
            if config.get("use_env"):
                # Reference existing env var
                lines.append("    env:")
                lines.append(f"      {config['use_env']}: ${{{config['use_env']}}}")
            elif config.get("value") and env_var:
                # Store as env var reference (user should export it)
                lines.append("    env:")
                lines.append(f"      {env_var}: ${{{env_var}}}")

        lines.append("    idle_ttl_s: 300")
        lines.append("")

    # Add health check settings
    lines.extend(
        [
            "# Health monitoring",
            "health_check:",
            "  enabled: true",
            "  interval_s: 30",
            "",
            "# Logging",
            "logging:",
            "  level: INFO",
            "  json_format: false",
        ]
    )

    return "\n".join(lines)


def _generate_claude_desktop_config(hangar_config_path: Path) -> dict:
    """Generate Claude Desktop MCP configuration for MCP Hangar.

    Args:
        hangar_config_path: Path to the MCP Hangar config file

    Returns:
        Claude Desktop mcpServers configuration
    """
    return {
        "mcpServers": {
            "mcp-hangar": {
                "command": "mcp-hangar",
                "args": ["serve", "--config", str(hangar_config_path)],
            }
        }
    }


def _backup_file(path: Path) -> Path | None:
    """Create a timestamped backup of a file.

    Args:
        path: Path to the file to backup

    Returns:
        Path to the backup file, or None if file doesn't exist
    """
    if not path.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".backup.{timestamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    return backup_path


def _show_completion_summary(
    providers: list[str],
    hangar_config_path: Path,
    claude_config_path: Path | None,
    backup_path: Path | None,
):
    """Display completion summary with next steps."""
    console.print()

    # Summary panel
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

    # Enabled providers
    if providers:
        console.print("\n[bold]Enabled providers:[/bold]")
        for name in providers:
            console.print(f"  [green]+[/green] {name}")

    # Next steps
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
        typer.Option(
            "--non-interactive",
            "-y",
            help="Run without prompts, using defaults",
        ),
    ] = False,
    bundle: Annotated[
        str | None,
        typer.Option(
            "--bundle",
            "-b",
            help="Provider bundle to install: starter, developer, data",
        ),
    ] = None,
    providers_opt: Annotated[
        str | None,
        typer.Option(
            "--providers",
            help="Comma-separated list of providers to install",
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config-path",
            help="Custom path for MCP Hangar config file",
        ),
    ] = None,
    claude_config_path: Annotated[
        Path | None,
        typer.Option(
            "--claude-config",
            help="Custom path to Claude Desktop config",
        ),
    ] = None,
    skip_claude: Annotated[
        bool,
        typer.Option(
            "--skip-claude",
            help="Skip Claude Desktop config modification",
        ),
    ] = False,
    reset: Annotated[
        bool,
        typer.Option(
            "--reset",
            help="Reset existing configuration",
        ),
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

    if claude_config_path:
        claude_config_file = claude_config_path
        existing_claude_config = None
        if claude_config_file.exists():
            try:
                with open(claude_config_file) as f:
                    existing_claude_config = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
    else:
        claude_config_file, existing_claude_config = _detect_claude_desktop()

    if claude_config_file:
        console.print(f"  [green]Found:[/green] {claude_config_file}")
        if existing_claude_config and existing_claude_config.get("mcpServers"):
            server_count = len(existing_claude_config["mcpServers"])
            console.print(f"  [dim]Existing MCP servers: {server_count}[/dim]")
    elif not skip_claude:
        if non_interactive:
            console.print("  [yellow]Claude Desktop not found - skipping integration[/yellow]")
            skip_claude = True
        else:
            searched = [str(p) for p in CLAUDE_DESKTOP_PATHS.values()]
            console.print("  [yellow]Claude Desktop not found[/yellow]")
            console.print(f"  [dim]Searched: {', '.join(searched)}[/dim]")

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
        # Use explicit provider list
        selected_providers = [p.strip() for p in providers_opt.split(",")]
        console.print(f"  Using providers: {', '.join(selected_providers)}")
    elif bundle:
        # Use bundle
        bundle_map = {
            "starter": ["filesystem", "fetch", "memory"],
            "developer": ["filesystem", "fetch", "memory", "github", "git"],
            "data": ["filesystem", "fetch", "memory", "sqlite", "postgres"],
        }
        if bundle.lower() not in bundle_map:
            raise CLIError(
                message=f"Unknown bundle: {bundle}",
                reason=f"Available bundles: {', '.join(bundle_map.keys())}",
                suggestions=["Use --bundle=starter, --bundle=developer, or --bundle=data"],
            )
        selected_providers = bundle_map[bundle.lower()]
        console.print(f"  Using '{bundle}' bundle: {', '.join(selected_providers)}")
    elif non_interactive:
        # Default to starter bundle
        selected_providers = ["filesystem", "fetch", "memory"]
        console.print(f"  Using default providers: {', '.join(selected_providers)}")
    else:
        # Interactive selection
        selected_providers = _prompt_provider_selection()

    if not selected_providers:
        console.print("  [yellow]No providers selected[/yellow]")
        if not non_interactive:
            proceed = questionary.confirm(
                "Continue with empty configuration?",
                default=False,
            ).ask()
            if not proceed:
                raise typer.Abort()

    # Step 3: Collect provider configurations
    if selected_providers and not non_interactive:
        console.print("\n[bold]Step 3:[/bold] Configuring providers...")

        categories = _get_provider_categories()
        provider_defs = {}
        for cat_providers in categories.values():
            for p in cat_providers:
                provider_defs[p["name"]] = p

        for name in selected_providers:
            provider_def = provider_defs.get(name, {})
            if provider_def.get("requires_config"):
                config = _collect_provider_config(name, provider_def)
                if config is None:
                    # User skipped this provider
                    selected_providers.remove(name)
                else:
                    provider_configs[name] = config

    # Step 4: Generate configuration files
    console.print("\n[bold]Step 4:[/bold] Generating configuration...")

    # Determine config path (command arg > global --config > default)
    hangar_config_path = config_path or global_opts.config or DEFAULT_CONFIG_PATH

    # Create config directory
    hangar_config_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing config if present and not resetting
    backup_path = None
    if hangar_config_path.exists() and not reset:
        if not non_interactive:
            overwrite = questionary.confirm(
                f"Configuration exists at {hangar_config_path}. Overwrite?",
                default=False,
            ).ask()
            if not overwrite:
                raise typer.Abort()
        backup_path = _backup_file(hangar_config_path)
        if backup_path:
            console.print(f"  [dim]Backed up to: {backup_path}[/dim]")

    # Write MCP Hangar config
    hangar_config_content = _generate_hangar_config(selected_providers, provider_configs)
    try:
        with open(hangar_config_path, "w") as f:
            f.write(hangar_config_content)
        console.print(f"  [green]Created:[/green] {hangar_config_path}")
    except OSError as e:
        raise PermissionError(str(hangar_config_path), "write") from e

    # Step 5: Update Claude Desktop config
    claude_backup_path = None
    if not skip_claude and claude_config_file:
        console.print("\n[bold]Step 5:[/bold] Updating Claude Desktop...")

        # Backup existing Claude config
        claude_backup_path = _backup_file(claude_config_file)
        if claude_backup_path:
            console.print(f"  [dim]Backed up to: {claude_backup_path}[/dim]")

        # Merge with existing config
        new_claude_config = existing_claude_config.copy() if existing_claude_config else {}
        hangar_mcp_config = _generate_claude_desktop_config(hangar_config_path)
        new_claude_config["mcpServers"] = hangar_mcp_config["mcpServers"]

        try:
            with open(claude_config_file, "w") as f:
                json.dump(new_claude_config, f, indent=2)
            console.print(f"  [green]Updated:[/green] {claude_config_file}")
        except OSError as e:
            raise PermissionError(str(claude_config_file), "write") from e

    # Step 6: Completion summary
    _show_completion_summary(
        providers=selected_providers,
        hangar_config_path=hangar_config_path,
        claude_config_path=claude_config_file if not skip_claude else None,
        backup_path=claude_backup_path or backup_path,
    )


__all__ = ["app", "init_command"]
