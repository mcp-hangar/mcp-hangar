"""Completion command - Generate shell completion scripts.

Generates completion scripts for bash, zsh, and fish shells to enable
tab completion for mcp-hangar commands.

The scripts are the ones Click generates for the real command tree, so they
cannot name a command or an option that does not exist, or miss one that does.
They used to be written out by hand in this file and had drifted: no `pin`,
`auth` or `config`, an init option that never existed after the provider ->
mcp_server rename, and servers the registry no longer offers (#1679).
"""

from typing import Annotated, Any, cast

import typer
from rich.console import Console

app = typer.Typer(
    name="completion",
    help="Generate shell completion scripts",
)

console = Console()

PROG_NAME = "mcp-hangar"
COMPLETE_VAR = "_MCP_HANGAR_COMPLETE"
SHELLS = ("bash", "zsh", "fish")


def completion_script(shell: str) -> str:
    """The completion script for *shell*, generated from the real command tree."""
    import typer.completion  # noqa: F401 -- registers Typer's completion classes with Click
    from click.shell_completion import get_completion_class
    from typer.main import get_command

    from ..main import app as root_app

    completer = get_completion_class(shell)
    if completer is None:
        raise ValueError(f"unsupported shell: {shell}")
    return str(completer(cast(Any, get_command(root_app)), {}, PROG_NAME, COMPLETE_VAR).source()).strip()


@app.command("bash")
def completion_bash():
    """Generate bash completion script.

    Usage:
        mcp-hangar completion bash >> ~/.bashrc
        # or
        mcp-hangar completion bash > /etc/bash_completion.d/mcp-hangar
    """
    print(completion_script("bash"))


@app.command("zsh")
def completion_zsh():
    """Generate zsh completion script.

    Usage:
        mcp-hangar completion zsh > ~/.zfunc/_mcp-hangar
        # Make sure ~/.zfunc is in your fpath
    """
    print(completion_script("zsh"))


@app.command("fish")
def completion_fish():
    """Generate fish completion script.

    Usage:
        mcp-hangar completion fish > ~/.config/fish/completions/mcp-hangar.fish
    """
    print(completion_script("fish"))


@app.command("install")
def completion_install(
    shell: Annotated[
        str | None,
        typer.Argument(
            help="Shell to install completion for (bash, zsh, fish)",
        ),
    ] = None,
):
    """Install completion script for your shell.

    Automatically detects your shell if not specified.

    Examples:
        mcp-hangar completion install
        mcp-hangar completion install zsh
    """
    import os
    from pathlib import Path

    # Detect shell if not specified
    if not shell:
        shell_path = os.environ.get("SHELL", "")
        if "zsh" in shell_path:
            shell = "zsh"
        elif "fish" in shell_path:
            shell = "fish"
        else:
            shell = "bash"

    console.print(f"Installing completion for [bold]{shell}[/bold]...")

    if shell == "bash":
        # Try to install to bash_completion.d or .bashrc
        completion_dir = Path("/etc/bash_completion.d")
        if completion_dir.exists() and os.access(completion_dir, os.W_OK):
            target = completion_dir / "mcp-hangar"
        else:
            target = Path.home() / ".local" / "share" / "bash-completion" / "completions" / "mcp-hangar"
            target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, "w") as f:
            f.write(completion_script("bash") + "\n")

        console.print(f"[green]Installed to {target}[/green]")
        console.print("Restart your shell or run: source ~/.bashrc")

    elif shell == "zsh":
        # Install to .zfunc
        zfunc = Path.home() / ".zfunc"
        zfunc.mkdir(exist_ok=True)
        target = zfunc / "_mcp-hangar"

        with open(target, "w") as f:
            f.write(completion_script("zsh") + "\n")

        console.print(f"[green]Installed to {target}[/green]")
        console.print("Add this to your ~/.zshrc if not already present:")
        console.print("  fpath=(~/.zfunc $fpath)")
        console.print("  autoload -Uz compinit && compinit")

    elif shell == "fish":
        # Install to fish completions
        target = Path.home() / ".config" / "fish" / "completions" / "mcp-hangar.fish"
        target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, "w") as f:
            f.write(completion_script("fish") + "\n")

        console.print(f"[green]Installed to {target}[/green]")
        console.print("Completion will be available in new fish sessions")

    else:
        console.print(f"[red]Unknown shell: {shell}[/red]")
        console.print("Supported shells: bash, zsh, fish")
        raise typer.Exit(1)


__all__ = ["app"]
