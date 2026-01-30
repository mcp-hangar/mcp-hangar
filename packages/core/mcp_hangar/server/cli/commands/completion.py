"""Completion command - Generate shell completion scripts.

Generates completion scripts for bash, zsh, and fish shells to enable
tab completion for mcp-hangar commands.
"""

from typing import Annotated

from rich.console import Console
import typer

app = typer.Typer(
    name="completion",
    help="Generate shell completion scripts",
)

console = Console()


# Completion script templates
# Note: These are raw shell scripts, line lengths are intentionally long in some places
# to match shell script conventions. The noqa comments disable line length checks.
BASH_COMPLETION = """
# MCP Hangar bash completion
# Add to ~/.bashrc or ~/.bash_completion

_mcp_hangar_completion() {
    local cur prev words cword
    _init_completion || return

    local commands="init status add remove serve completion"
    local global_opts="--config --verbose --quiet --json --version --help"
    local init_opts="--non-interactive --bundle --providers --config-path"
    init_opts="$init_opts --claude-config --skip-claude --reset --help"
    local add_opts="--search --yes --no-reload --help"
    local add_providers="filesystem fetch memory github git sqlite postgres"

    case "${prev}" in
        mcp-hangar)
            COMPREPLY=($(compgen -W "${commands} ${global_opts}" -- "${cur}"))
            return 0
            ;;
        --config|-c)
            _filedir yaml
            return 0
            ;;
        init)
            COMPREPLY=($(compgen -W "${init_opts}" -- "${cur}"))
            return 0
            ;;
        --bundle|-b)
            COMPREPLY=($(compgen -W "starter developer data" -- "${cur}"))
            return 0
            ;;
        status)
            COMPREPLY=($(compgen -W "--watch --interval --details --help" -- "${cur}"))
            return 0
            ;;
        add)
            COMPREPLY=($(compgen -W "${add_opts} ${add_providers}" -- "${cur}"))
            return 0
            ;;
        remove)
            COMPREPLY=($(compgen -W "--yes --keep-running --help" -- "${cur}"))
            return 0
            ;;
        serve)
            local serve_opts="--http --host --port --log-file --log-level --json-logs --help"
            COMPREPLY=($(compgen -W "${serve_opts}" -- "${cur}"))
            return 0
            ;;
        --log-level)
            COMPREPLY=($(compgen -W "DEBUG INFO WARNING ERROR CRITICAL" -- "${cur}"))
            return 0
            ;;
        completion)
            COMPREPLY=($(compgen -W "bash zsh fish install --help" -- "${cur}"))
            return 0
            ;;
    esac

    if [[ "${cur}" == -* ]]; then
        COMPREPLY=($(compgen -W "${global_opts}" -- "${cur}"))
    else
        COMPREPLY=($(compgen -W "${commands}" -- "${cur}"))
    fi
}

complete -F _mcp_hangar_completion mcp-hangar
"""

ZSH_COMPLETION = """
#compdef mcp-hangar

# MCP Hangar zsh completion
# Add to ~/.zshrc or place in fpath directory

_mcp_hangar() {
    local -a commands
    commands=(
        'init:Initialize MCP Hangar with interactive setup wizard'
        'status:Show status of all providers'
        'add:Add a provider from the MCP Registry'
        'remove:Remove a provider from configuration'
        'serve:Start the MCP Hangar server'
        'completion:Generate shell completion scripts'
    )

    local -a global_opts
    global_opts=(
        '(-c --config)'{-c,--config}'[Path to config.yaml]:config:_files -g "*.yaml"'
        '(-v --verbose)'{-v,--verbose}'[Show verbose output]'
        '(-q --quiet)'{-q,--quiet}'[Suppress non-essential output]'
        '--json[Output in JSON format]'
        '(-V --version)'{-V,--version}'[Show version and exit]'
        '(-h --help)'{-h,--help}'[Show help]'
    )

    _arguments -C \\
        $global_opts \\
        '1: :->command' \\
        '*::arg:->args'

    case $state in
        command)
            _describe -t commands 'mcp-hangar command' commands
            ;;
        args)
            case $words[1] in
                init)
                    _arguments \\
                        '(-y --non-interactive)'{-y,--non-interactive}'[Run without prompts]' \\
                        '(-b --bundle)'{-b,--bundle}'[Provider bundle]:bundle:(starter developer data)' \\
                        '--providers[Comma-separated providers]:providers:' \\
                        '--config-path[Custom config path]:path:_files' \\
                        '--claude-config[Claude Desktop config path]:path:_files' \\
                        '--skip-claude[Skip Claude Desktop integration]' \\
                        '--reset[Reset existing configuration]'
                    ;;
                status)
                    _arguments \\
                        '1::provider:' \\
                        '(-w --watch)'{-w,--watch}'[Continuously update display]' \\
                        '(-i --interval)'{-i,--interval}'[Update interval]:seconds:' \\
                        '(-d --details)'{-d,--details}'[Show additional columns]'
                    ;;
                add)
                    local providers
                    providers=(filesystem fetch memory github git sqlite postgres)
                    _arguments \\
                        "1:provider:($providers)" \\
                        '(-s --search)'{-s,--search}'[Search registry]' \\
                        '(-y --yes)'{-y,--yes}'[Skip confirmation]' \\
                        '--no-reload[Skip hot reload]'
                    ;;
                remove)
                    _arguments \\
                        '1:provider:' \\
                        '(-y --yes)'{-y,--yes}'[Skip confirmation]' \\
                        '--keep-running[Keep provider running]'
                    ;;
                serve)
                    _arguments \\
                        '--http[Run in HTTP mode]' \\
                        '--host[HTTP host]:host:' \\
                        '(-p --port)'{-p,--port}'[HTTP port]:port:' \\
                        '--log-file[Log file path]:file:_files' \\
                        '--log-level[Log level]:level:(DEBUG INFO WARNING ERROR CRITICAL)' \\
                        '--json-logs[JSON log format]'
                    ;;
                completion)
                    _arguments \\
                        '1:shell:(bash zsh fish)' \\
                        '--install[Install completion]'
                    ;;
            esac
            ;;
    esac
}

_mcp_hangar "$@"
"""

FISH_COMPLETION = """
# MCP Hangar fish completion
# Save to ~/.config/fish/completions/mcp-hangar.fish

# Disable file completion by default
complete -c mcp-hangar -f

# Global options
complete -c mcp-hangar -s c -l config -d "Path to config.yaml file" -r -F
complete -c mcp-hangar -s v -l verbose -d "Show verbose output"
complete -c mcp-hangar -s q -l quiet -d "Suppress non-essential output"
complete -c mcp-hangar -l json -d "Output in JSON format"
complete -c mcp-hangar -s V -l version -d "Show version and exit"
complete -c mcp-hangar -s h -l help -d "Show help"

# Commands
complete -c mcp-hangar -n "__fish_use_subcommand" -a "init" -d "Initialize MCP Hangar"
complete -c mcp-hangar -n "__fish_use_subcommand" -a "status" -d "Show provider status"
complete -c mcp-hangar -n "__fish_use_subcommand" -a "add" -d "Add a provider"
complete -c mcp-hangar -n "__fish_use_subcommand" -a "remove" -d "Remove a provider"
complete -c mcp-hangar -n "__fish_use_subcommand" -a "serve" -d "Start the server"
complete -c mcp-hangar -n "__fish_use_subcommand" -a "completion" -d "Generate completions"

# init options
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -s y -l non-interactive \\
    -d "Run without prompts"
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -s b -l bundle \\
    -d "Provider bundle" -r -a "starter developer data"
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -l providers \\
    -d "Comma-separated providers" -r
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -l config-path \\
    -d "Custom config path" -r -F
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -l claude-config \\
    -d "Claude Desktop config" -r -F
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -l skip-claude \\
    -d "Skip Claude integration"
complete -c mcp-hangar -n "__fish_seen_subcommand_from init" -l reset -d "Reset configuration"

# status options
complete -c mcp-hangar -n "__fish_seen_subcommand_from status" -s w -l watch -d "Watch mode"
complete -c mcp-hangar -n "__fish_seen_subcommand_from status" -s i -l interval \\
    -d "Update interval" -r
complete -c mcp-hangar -n "__fish_seen_subcommand_from status" -s d -l details -d "Show details"

# add options
complete -c mcp-hangar -n "__fish_seen_subcommand_from add" -s s -l search -d "Search registry"
complete -c mcp-hangar -n "__fish_seen_subcommand_from add" -s y -l yes -d "Skip confirmation"
complete -c mcp-hangar -n "__fish_seen_subcommand_from add" -l no-reload -d "Skip hot reload"
complete -c mcp-hangar -n "__fish_seen_subcommand_from add" \\
    -a "filesystem fetch memory github git sqlite postgres"

# remove options
complete -c mcp-hangar -n "__fish_seen_subcommand_from remove" -s y -l yes -d "Skip confirmation"
complete -c mcp-hangar -n "__fish_seen_subcommand_from remove" -l keep-running \\
    -d "Keep provider running"

# serve options
complete -c mcp-hangar -n "__fish_seen_subcommand_from serve" -l http -d "HTTP mode"
complete -c mcp-hangar -n "__fish_seen_subcommand_from serve" -l host -d "HTTP host" -r
complete -c mcp-hangar -n "__fish_seen_subcommand_from serve" -s p -l port -d "HTTP port" -r
complete -c mcp-hangar -n "__fish_seen_subcommand_from serve" -l log-file -d "Log file" -r -F
complete -c mcp-hangar -n "__fish_seen_subcommand_from serve" -l log-level \\
    -d "Log level" -r -a "DEBUG INFO WARNING ERROR CRITICAL"
complete -c mcp-hangar -n "__fish_seen_subcommand_from serve" -l json-logs -d "JSON logs"

# completion options
complete -c mcp-hangar -n "__fish_seen_subcommand_from completion" -a "bash zsh fish"
complete -c mcp-hangar -n "__fish_seen_subcommand_from completion" -l install -d "Install completion"
"""


@app.command("bash")
def completion_bash():
    """Generate bash completion script.

    Usage:
        mcp-hangar completion bash >> ~/.bashrc
        # or
        mcp-hangar completion bash > /etc/bash_completion.d/mcp-hangar
    """
    print(BASH_COMPLETION.strip())


@app.command("zsh")
def completion_zsh():
    """Generate zsh completion script.

    Usage:
        mcp-hangar completion zsh > ~/.zfunc/_mcp-hangar
        # Make sure ~/.zfunc is in your fpath
    """
    print(ZSH_COMPLETION.strip())


@app.command("fish")
def completion_fish():
    """Generate fish completion script.

    Usage:
        mcp-hangar completion fish > ~/.config/fish/completions/mcp-hangar.fish
    """
    print(FISH_COMPLETION.strip())


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
            f.write(BASH_COMPLETION.strip())

        console.print(f"[green]Installed to {target}[/green]")
        console.print("Restart your shell or run: source ~/.bashrc")

    elif shell == "zsh":
        # Install to .zfunc
        zfunc = Path.home() / ".zfunc"
        zfunc.mkdir(exist_ok=True)
        target = zfunc / "_mcp-hangar"

        with open(target, "w") as f:
            f.write(ZSH_COMPLETION.strip())

        console.print(f"[green]Installed to {target}[/green]")
        console.print("Add this to your ~/.zshrc if not already present:")
        console.print("  fpath=(~/.zfunc $fpath)")
        console.print("  autoload -Uz compinit && compinit")

    elif shell == "fish":
        # Install to fish completions
        target = Path.home() / ".config" / "fish" / "completions" / "mcp-hangar.fish"
        target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, "w") as f:
            f.write(FISH_COMPLETION.strip())

        console.print(f"[green]Installed to {target}[/green]")
        console.print("Completion will be available in new fish sessions")

    else:
        console.print(f"[red]Unknown shell: {shell}[/red]")
        console.print("Supported shells: bash, zsh, fish")
        raise typer.Exit(1)


__all__ = ["app"]
