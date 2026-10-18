"""Why command - show why a call was refused (#1777).

A first deny leaves the user with nothing to look at: the refusal is a line on
the stderr of a process the client spawned, and a record in the event store.
`why` reads that store, read-only, so it works while the gateway has it open.
"""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from ..config_path import resolve_config_path
from ..main import GlobalOptions
from ..services.refusal_log import (
    EventStoreNotReadable,
    PinEntry,
    Refusal,
    default_path,
    event_store_path,
    read_pin_history,
    read_refusals,
)

console = Console()


def _store_path(db: Path | None, global_opts: GlobalOptions) -> Path:
    """The event store to read: --db, else the one the config names, else where init puts it."""
    if db is not None:
        return db
    resolved = resolve_config_path(global_opts.config).path
    if not resolved.is_file():
        return default_path()
    try:
        config = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise EventStoreNotReadable(f"{resolved} is not valid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise EventStoreNotReadable(f"{resolved} does not contain a configuration mapping.")
    if not (config.get("event_store") or config.get("persistence")):
        return default_path()
    return event_store_path(config)


def _when(refusal: Refusal) -> str:
    return refusal.occurred_at.strftime("%Y-%m-%d %H:%M:%S") if refusal.occurred_at else "?"


def _show_one(refusal: Refusal) -> None:
    console.print(f"[bold]{refusal.event_type}[/bold]  {_when(refusal)} UTC")
    for key, value in refusal.fields.items():
        console.print(f"  {key}: {value}")


def _show_table(refusals: list[Refusal]) -> None:
    table = Table(show_header=True, header_style="bold")
    for column in ("Time (UTC)", "Event", "Server", "Tool", "Gate", "Reason"):
        table.add_column(column, overflow="fold")
    for refusal in refusals:
        table.add_row(
            _when(refusal),
            refusal.event_id[:8],
            refusal.server,
            refusal.tool,
            refusal.gate,
            refusal.reason,
        )
    console.print(table)
    for refusal in refusals:
        if refusal.expected_digest is not None or refusal.observed_digest is not None:
            console.print(
                f"  {refusal.event_id[:8]}  expected {refusal.expected_digest or '(no pin entry)'}"
                f"  observed {refusal.observed_digest or '(none)'}"
            )


def _show_pin_history(entries: list[PinEntry]) -> None:
    table = Table(show_header=True, header_style="bold")
    for column in ("Time (UTC)", "Event", "Server", "Tenant", "Digest", "Replaced", "Upstream", "By"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        table.add_row(
            entry.occurred_at.strftime("%Y-%m-%d %H:%M:%S") if entry.occurred_at else "?",
            entry.event,
            entry.server,
            entry.tenant_id or "(all)",
            entry.sha256[:12],
            (entry.previous_sha256 or "")[:12],
            entry.upstream or "",
            entry.approved_by,
        )
    console.print(table)
    for entry in entries:
        if entry.structure_sha256 or entry.description_sha256:
            console.print(
                f"  {entry.sha256[:12]}  structure {(entry.structure_sha256 or '-')[:12]}"
                f"  description {(entry.description_sha256 or '-')[:12]}"
            )


def why_command(
    ctx: typer.Context,
    event_id: Annotated[
        str | None,
        typer.Argument(help="Show one refusal in full, by its event id (the first characters are enough)."),
    ] = None,
    last: Annotated[int, typer.Option("--last", "-n", min=1, help="How many of the latest refusals to show.")] = 10,
    server: Annotated[str | None, typer.Option("--server", "-s", help="Only refusals for this server.")] = None,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Read this event store file instead of the one the config names."),
    ] = None,
    tool: Annotated[
        str | None,
        typer.Option("--tool", "-t", help="Show the history of this tool's pin instead: each pin recorded or revoked."),
    ] = None,
) -> None:
    """Show why calls were refused: the gate, the reason and, for a digest mismatch, both digests.

    Reads the SQLite event store without writing to it, so it works while a
    gateway your client started has it open.

    Examples:
        mcp-hangar why
        mcp-hangar why --last 1
        mcp-hangar why --server filesystem
        mcp-hangar why 3f2a9c1b
        mcp-hangar why --tool read_file
    """
    global_opts: GlobalOptions = ctx.obj if ctx.obj else GlobalOptions()
    try:
        path = _store_path(db, global_opts)
        if tool is not None:
            history = read_pin_history(path, tool, server=server)
            if not history:
                console.print(f"No pin recorded for {tool!r} in {path}.")
                return
            _show_pin_history(history)
            return
        refusals = read_refusals(path, last=last, server=server, event_id=event_id)
    except EventStoreNotReadable as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if not refusals:
        console.print(f"No refusals recorded in {path}.")
        return
    if event_id is not None:
        for refusal in refusals:
            _show_one(refusal)
        return
    _show_table(refusals)


__all__ = ["why_command"]
