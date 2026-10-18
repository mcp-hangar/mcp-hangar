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

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console

from ....domain.exceptions import ConfigurationError
from ....pin_recheck import pin_recheck_interval_s
from ....server.config import _max_consecutive_failures, build_config, check_process_config
from ....server.config_schema import validate_config
from ..config_path import DEFAULT_RULE_HELP, resolve_config_path


def _value_problems(config: dict) -> list[str]:
    """Values the boot refuses, found without booting (#1733).

    `validate_config` reads key names only, so `pin_recheck_interval_s: 2` or
    `max_consecutive_failures: 500` passed `config check` and then refused the
    gateway. These run the checks the boot and a reload run: the process-wide
    sections, then the `mcp_servers` section built the way a reload builds it,
    which stages everything and puts nothing in force. A value the loader only
    warns about and defaults is not a refusal and is not reported here.
    """
    problems: list[str] = []

    def collect(check: Callable[[], object], *errors: type[Exception]) -> None:
        try:
            check()
        except errors as exc:
            if str(exc) not in problems:
                problems.append(str(exc))

    collect(lambda: pin_recheck_interval_s(config), ValueError)
    collect(lambda: check_process_config(config), ConfigurationError, ValueError)
    servers = config.get("mcp_servers")
    if isinstance(servers, dict):
        for server_id, spec in servers.items():
            if isinstance(spec, dict) and spec.get("mode") != "group" and "max_consecutive_failures" in spec:
                collect(partial(_max_consecutive_failures, str(server_id), spec), ConfigurationError)
        collect(lambda: build_config(servers), ConfigurationError)
    return problems


app = typer.Typer(name="config", help="Inspect and validate configuration")

console = Console()


@app.command(name="check")
def check_command(
    ctx: typer.Context,
    config_path: Annotated[
        Path,
        typer.Argument(help="Path to config.yaml. " + DEFAULT_RULE_HELP),
    ] = None,  # type: ignore[assignment]
) -> None:
    """Report configuration keys that nothing reads and values the boot refuses.

    Exit code 0 = every key is known and every checked value is accepted, 1 = not, 2 = the file is
    missing or is not YAML.
    """
    # The positional path, then the global --config (#1682), then the shared default.
    path = resolve_config_path(config_path, getattr(ctx.obj, "config", None)).path

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
    value_problems = _value_problems(config)

    if not problems and not value_problems:
        console.print(f"[green]OK[/green] {path}: every key is one Hangar reads and every checked value is accepted.")
        return

    if problems:
        console.print(f"[red]FAIL[/red] {path}: {len(problems)} key(s) nothing reads:\n")
        for problem in problems:
            console.print(f"  {problem}")
        console.print(
            "\nA key Hangar does not read is kept and ignored, so the setting simply "
            "does not apply. Check the spelling against the allowed set above."
        )
    if value_problems:
        console.print(f"[red]FAIL[/red] {path}: {len(value_problems)} value(s) the gateway refuses at boot:\n")
        for problem in value_problems:
            console.print(f"  {problem}")
    raise typer.Exit(1)
