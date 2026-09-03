"""Pin command -- compute, write and check the digests Hangar enforces (#1191).

Hangar refuses a tool whose schema no longer matches its pin, and has since
2.0.0. Nothing computed a pin for you: `compute_tool_digest` was reachable from
the projection registry and from nowhere a user could type, so adopting the
feature meant reproducing an RFC 8785 canonicalization by hand. This command is
the missing half, and it asks the servers rather than the configuration --
a tool list is what a server answers.

    mcp-hangar pin                 # print {tool: sha256} per server
    mcp-hangar pin --write         # merge them into the config
    mcp-hangar pin --check         # exit 1 when the file and the servers disagree

Exit codes: 0 agreement (or a successful write), 1 drift, 2 the question could
not be answered -- no such config, unreadable YAML, unknown `--server`, or a
server that never came up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer
import yaml

from ..services.pinning import (
    Observation,
    find_drift,
    merge_pins,
    observe_digests,
    write_config_with_pins,
)

console = Console()


def pin_command(
    config_path: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config.yaml. Defaults to $MCP_CONFIG, else ./config.yaml."),
    ] = None,
    mcp_server: Annotated[
        list[str] | None,
        typer.Option("--server", "-s", help="Only this server. Repeatable; default is every configured server."),
    ] = None,
    write: Annotated[
        bool,
        typer.Option("--write", help="Merge the observed digests into the config file."),
    ] = False,
    check: Annotated[
        bool,
        typer.Option("--check", help="Exit 1 when a configured pin disagrees with what the server serves."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Machine-readable output."),
    ] = False,
) -> None:
    """Compute the digest of every tool your servers serve."""
    if write and check:
        console.print("[red]--write and --check ask different questions; pass one.[/red]")
        raise typer.Exit(2)

    path = Path(config_path or os.getenv("MCP_CONFIG") or "config.yaml")
    if not path.is_file():
        console.print(f"[red]No such configuration file:[/red] {path}")
        raise typer.Exit(2)

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        console.print(f"[red]{path} is not valid YAML:[/red] {exc}")
        raise typer.Exit(2) from exc
    if not isinstance(document, dict):
        console.print(f"[red]{path} does not contain a configuration mapping.[/red]")
        raise typer.Exit(2)

    try:
        observations = observe_digests(path, mcp_server_ids=list(mcp_server or []))
    except KeyError as exc:
        console.print(f"[red]No such mcp_server in {path}:[/red] {exc.args[0]}")
        raise typer.Exit(2) from exc

    if not observations:
        console.print(f"[yellow]{path} configures no mcp_servers, so there is nothing to pin.[/yellow]")
        raise typer.Exit(2)

    failed = [observation for observation in observations if not observation.ok]

    if check:
        _report_check(document, observations, failed, as_json)
        return
    if write:
        _report_write(path, document, observations, failed, as_json)
        return
    _report_digests(observations, failed, as_json)


def _report_digests(observations: list[Observation], failed: list[Observation], as_json: bool) -> None:
    digests = {o.mcp_server_id: o.digests for o in observations if o.ok}

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "digests": digests,
                    "unreachable": {o.mcp_server_id: o.error for o in failed},
                }
            )
        )
    else:
        if digests:
            console.print(yaml.safe_dump(digests, default_flow_style=False, sort_keys=True).rstrip())
        for observation in failed:
            console.print(f"[red]{observation.mcp_server_id}: {observation.error}[/red]")

    # A digest nobody could compute is not an answer to "what should I pin".
    raise typer.Exit(2 if failed else 0)


def _report_write(
    path: Path,
    document: dict,
    observations: list[Observation],
    failed: list[Observation],
    as_json: bool,
) -> None:
    backup = write_config_with_pins(path, merge_pins(document, observations))
    written = {o.mcp_server_id: len(o.digests) for o in observations if o.ok}

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "written": written,
                    "backup": str(backup),
                    "unreachable": {o.mcp_server_id: o.error for o in failed},
                }
            )
        )
    else:
        for server_id, count in sorted(written.items()):
            console.print(f"[green]pinned[/green] {server_id}: {count} tool(s)")
        for observation in failed:
            console.print(f"[red]{observation.mcp_server_id}: {observation.error} -- pins left as they were[/red]")
        console.print(f"[dim]previous config kept at {backup}; comments and key order are not preserved[/dim]")

    raise typer.Exit(2 if failed else 0)


def _report_check(
    document: dict,
    observations: list[Observation],
    failed: list[Observation],
    as_json: bool,
) -> None:
    drift = find_drift(document, observations)

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "drift": [
                        {
                            "mcp_server": d.mcp_server_id,
                            "tool": d.tool,
                            "expected": d.expected,
                            "observed": d.observed,
                        }
                        for d in drift
                    ],
                    "unreachable": {o.mcp_server_id: o.error for o in failed},
                }
            )
        )
    else:
        for d in drift:
            observed = d.observed or "(no longer served)"
            console.print(
                f"[red]drift[/red] {d.mcp_server_id}.{d.tool}\n  pinned:   {d.expected}\n  serving:  {observed}"
            )
        for observation in failed:
            console.print(f"[red]{observation.mcp_server_id}: {observation.error}[/red]")
        if not drift and not failed:
            console.print("[green]every pinned tool still matches its pin.[/green]")

    if failed:
        raise typer.Exit(2)
    raise typer.Exit(1 if drift else 0)


__all__ = ["pin_command"]
