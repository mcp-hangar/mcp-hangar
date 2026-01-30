"""Main CLI entry point for MCP Hangar.

This module provides the typer-based CLI application with all subcommands.
It maintains backward compatibility with the existing argparse-based CLI
by defaulting to server mode when no subcommand is specified.
"""

from pathlib import Path
import sys
from typing import Annotated

from rich.console import Console
import typer

from .errors import CLIError, handle_cli_error

# Global state
console = Console()
error_console = Console(stderr=True)

# Create the main app
app = typer.Typer(
    name="mcp-hangar",
    help="MCP Hangar - Production-grade MCP provider platform",
    no_args_is_help=False,  # Allow running without args (defaults to serve)
    add_completion=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,  # We handle errors ourselves
)


# Global options stored in context
class GlobalOptions:
    """Global CLI options available to all commands."""

    def __init__(
        self,
        config: Path | None = None,
        verbose: bool = False,
        quiet: bool = False,
        json_output: bool = False,
    ):
        self.config = config
        self.verbose = verbose
        self.quiet = quiet
        self.json_output = json_output


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to config.yaml file",
            envvar="MCP_CONFIG",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show verbose output including debug information",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress non-essential output",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output in JSON format for scripting",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit",
        ),
    ] = False,
):
    """MCP Hangar - Production-grade MCP provider platform.

    Run 'mcp-hangar init' for interactive setup, or 'mcp-hangar serve' to start the server.
    """
    # Handle version flag
    if version:
        from importlib.metadata import version as get_version

        try:
            ver = get_version("mcp-hangar")
        except Exception:
            ver = "unknown"
        console.print(f"mcp-hangar {ver}")
        raise typer.Exit(0)

    # Store global options in context
    ctx.ensure_object(dict)
    ctx.obj = GlobalOptions(
        config=config,
        verbose=verbose,
        quiet=quiet,
        json_output=json_output,
    )

    # If no subcommand, default to serve
    if ctx.invoked_subcommand is None:
        # Import and run the serve command
        from .commands.serve import serve_command

        serve_command(ctx)


# Import and register subcommand modules
def _register_commands():
    """Register all subcommand modules."""
    from .commands import add, completion, init, remove, serve, status

    app.add_typer(init.app, name="init")
    app.command(name="status")(status.status_command)
    app.command(name="add")(add.add_command)
    app.command(name="remove")(remove.remove_command)
    app.command(name="serve")(serve.serve_command)
    app.add_typer(completion.app, name="completion")


# Register commands on import
_register_commands()


def cli_main():
    """CLI entry point for mcp-hangar command.

    This function is called by the console_scripts entry point.
    It wraps the typer app with error handling.
    """
    try:
        app()
    except CLIError as e:
        handle_cli_error(e, error_console)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        error_console.print("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        # Unexpected error - show stack trace in verbose mode
        import traceback

        error_console.print(f"\n[red]Unexpected error:[/red] {e}")
        error_console.print("\nRun with --verbose for more details.")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            error_console.print("\n[dim]Stack trace:[/dim]")
            traceback.print_exc()
        sys.exit(2)


__all__ = ["app", "cli_main", "GlobalOptions", "console", "error_console"]
