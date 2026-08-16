"""Config command - answer "is this config valid" without starting a gateway.

Before this, the only way to find out whether `config.yaml` was right was to
start Hangar and watch what happened -- and for a misspelled key, what happened
was that it started fine and the setting did not apply. An operator could not
check a change before rolling it out, and CI could not check a config example at
all.

`config check` is always strict. The loader warns rather than refuses until
3.0.0 (see `server/config_schema.py`), but this command exists to be asked the
question directly, so it answers it.
"""

from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer
import yaml

from ....server.config_schema import validate_config

app = typer.Typer(name="config", help="Inspect and validate configuration")

console = Console()


@app.command(name="check")
def check_command(
    config_path: Annotated[
        Path,
        typer.Argument(help="Path to config.yaml. Defaults to $MCP_CONFIG, else ./config.yaml."),
    ] = None,  # type: ignore[assignment]
) -> None:
    """Report configuration keys that nothing reads.

    Exit code 0 = every key is known, 1 = at least one is not, 2 = the file is
    missing or is not YAML.
    """
    import os

    path = Path(config_path or os.getenv("MCP_CONFIG") or "config.yaml")

    if not path.is_file():
        console.print(f"[red]No such configuration file:[/red] {path}")
        raise typer.Exit(2)

    try:
        config = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        console.print(f"[red]{path} is not valid YAML:[/red] {exc}")
        raise typer.Exit(2) from exc

    if not isinstance(config, dict):
        console.print(f"[red]{path} does not contain a configuration mapping.[/red]")
        raise typer.Exit(2)

    problems = validate_config(config)

    if not problems:
        console.print(f"[green]OK[/green] {path}: every key is one Hangar reads.")
        return

    console.print(f"[red]FAIL[/red] {path}: {len(problems)} key(s) nothing reads:\n")
    for problem in problems:
        console.print(f"  {problem}")
    console.print(
        "\nA key Hangar does not read is kept and ignored, so the setting simply "
        "does not apply. Check the spelling against the allowed set above."
    )
    raise typer.Exit(1)
