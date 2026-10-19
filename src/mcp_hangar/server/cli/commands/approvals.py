"""Approvals command - list and resolve held approvals from the CLI (#1778).

A stdio gateway serves no REST API, so ``POST /api/approvals/{id}/resolve`` is
not there to call, and a call on an ``approval_list`` was held until it timed
out. This resolves it from a second process, through the approval store the
gateway already shares: the waiting gateway polls that store
(``ApprovalGateService``), sees the decision, publishes the same granted or
denied event a REST decision produces, and releases the call. There is no
listener and no IPC channel.

The decision goes through ``ResolveApprovalHandler``, the one chokepoint REST
uses, so expiry, the compare-and-swap (#1794) and tenant scoping are the same.
Two things differ, and both are deliberate:

* **Who decides is the declared principal, and nobody else.** The approver is
  ``auth.stdio.principal`` (ADR-026), built exactly as the gateway builds it. A
  configuration without one is refused: there is no fallback to a system or
  admin identity, and no flag to name another.
* **Its declared roles decide, whether or not auth is on** (maintainer decision,
  2026-10-19). REST with auth off grants everyone; here, as on the stdio
  management surface, a principal whose roles do not hold ``approval:resolve``
  is refused. ``viewer``, the role ``init`` declares, does not hold it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from ..errors import CLIError
from ..main import GlobalOptions

app = typer.Typer(
    name="approvals",
    help="List and resolve approvals held by a gateway that shares this config's approval store.",
    no_args_is_help=True,
)

console = Console()

_ROLES_KEY = "auth.stdio.principal.roles"


class _DeclaredRoles:
    """Auth components that decide on the declared principal's roles, auth on or off."""

    enabled = True

    def __init__(self, principal: Any) -> None:
        from mcp_hangar.auth.stdio_principal import authorizer_over_declared_roles

        self.authz_middleware = authorizer_over_declared_roles(principal)


def _load(global_opts: GlobalOptions, config: Path | None) -> tuple[Path, dict[str, Any]]:
    """The configuration file and its checked, interpolated contents -- without applying it."""
    import yaml

    from mcp_hangar.domain.exceptions import ConfigurationError
    from mcp_hangar.server.config import _read_config_file, prepare_config

    from ..config_path import require_config_file, resolve_config_path

    try:
        path = require_config_file(resolve_config_path(config, global_opts.config))
        return path, prepare_config(_read_config_file(str(path)), source=str(path))
    except (ConfigurationError, ValueError, OSError, yaml.YAMLError) as exc:
        raise CLIError(f"Could not read the configuration: {exc}") from exc


def _principal(full_config: dict[str, Any], config_path: Path) -> Any:
    from mcp_hangar.server.bootstrap import declared_stdio_principal

    principal = declared_stdio_principal(full_config)
    if principal is None:
        raise CLIError(
            f"{config_path} declares no auth.stdio.principal, so there is nobody to record as the approver.",
            suggestions=[
                "Declare the local caller under auth.stdio.principal (id, tenant_id, roles), as `mcp-hangar init` "
                "writes it, with a role that holds approval:resolve (provider-admin or admin)."
            ],
        )
    return principal


def _repository(full_config: dict[str, Any], db: Path | None) -> tuple[Any, str]:
    """The approval repository the gateway uses, and a description of where it is."""
    from mcp_hangar.server.bootstrap.components import approval_database_path
    from mcp_hangar.server.bootstrap.persistence import select_backend

    if db is None:
        try:
            backend = select_backend(full_config)
        except Exception as exc:  # noqa: BLE001 -- a storage config error becomes an actionable CLI error
            raise CLIError(f"Could not open the configured storage backend: {exc}") from exc
        if backend is not None:
            name = (full_config.get("persistence") or {}).get("backend")
            return backend.approval_repository(), f"persistence.backend: {name}"
        db = approval_database_path(full_config)
        if db is None:
            raise CLIError(
                "This configuration keeps approvals in data/mcp_hangar.db under the gateway's working "
                "directory, which this command cannot know.",
                suggestions=[
                    "Set event_store.path to an absolute path (as `mcp-hangar init` does); approvals then live "
                    "beside it.",
                    "Or name the file with --db.",
                ],
            )
    if not db.is_file():
        raise CLIError(
            f"No approval store at {db}.",
            reason="The gateway creates it the first time it holds a call.",
        )
    from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
    from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig

    return SqliteApprovalRepository(Database(DatabaseConfig(path=str(db)))), str(db)


def _visible(approval: Any, principal: Any) -> bool:
    """The REST listing's tenant rule: a tenantless approval is visible, else only to its tenant."""
    tenant = getattr(approval, "tenant_id", None)
    return tenant is None or tenant == principal.tenant_id


async def _match(repository: Any, principal: Any, approval_id: str) -> str:
    """A full approval id from *approval_id* or a unique prefix of a pending one."""
    if await repository.get(approval_id) is not None:
        return approval_id
    from mcp_hangar.approvals.models import ApprovalState

    pending = [r for r in await repository.list_by_state(ApprovalState.PENDING) if _visible(r, principal)]
    matches = [r.approval_id for r in pending if r.approval_id.startswith(approval_id)]
    if len(matches) > 1:
        raise CLIError(f"{approval_id!r} matches {len(matches)} pending approvals; give more of the id.")
    return matches[0] if matches else approval_id


_COLUMNS = ("approval_id", "mcp_server", "tool", "requested_by", "expires_at", "arguments", "decided_by")


def _row(r: Any) -> dict[str, Any]:
    """One approval as the listing shows it; the arguments are the redacted copy the store holds."""
    return {
        "approval_id": r.approval_id,
        "mcp_server": r.provider_id,
        "tool": r.tool_name,
        "requested_by": r.requested_by,
        "expires_at": r.expires_at.isoformat(),
        "arguments": r.arguments,
        "decided_by": r.decided_by,
        "state": r.state.value,
    }


def _list(global_opts: GlobalOptions, config: Path | None, db: Path | None, state: str) -> None:
    from mcp_hangar.approvals.models import ApprovalState

    try:
        wanted = ApprovalState(state)
    except ValueError as exc:
        raise CLIError(f"Unknown state {state!r}; one of: {', '.join(s.value for s in ApprovalState)}.") from exc
    config_path, full_config = _load(global_opts, config)
    principal = _principal(full_config, config_path)
    repository, where = _repository(full_config, db)
    approvals = [r for r in asyncio.run(repository.list_by_state(wanted)) if _visible(r, principal)]
    if global_opts.json_output:
        print(json.dumps([_row(r) for r in approvals]))
        return
    if not approvals:
        console.print(f"No {wanted.value} approvals in {where}.")
        return
    table = Table(show_header=True, header_style="bold")
    for column in ("ID", "Server", "Tool", "Requested by", "Expires (UTC)", "Arguments", "Decided by"):
        table.add_column(column, overflow="fold", no_wrap=column == "ID")
    for r in approvals:
        row = _row(r)
        row["expires_at"] = r.expires_at.strftime("%Y-%m-%d %H:%M:%S")
        row["arguments"] = json.dumps(r.arguments, sort_keys=True)
        table.add_row(*(row[k] or "" for k in _COLUMNS))
    console.print(f"Approval store: {where}")
    console.print(table)


def _resolve(
    global_opts: GlobalOptions,
    config: Path | None,
    db: Path | None,
    approval_id: str,
    approved: bool,
    reason: str | None,
) -> None:
    from mcp_hangar.approvals.commands.resolve import ResolveApprovalCommand, ResolveApprovalHandler, ResolveOutcome
    from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
    from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
    from mcp_hangar.approvals.service import ApprovalGateService
    from mcp_hangar.domain.exceptions import AccessDeniedError

    config_path, full_config = _load(global_opts, config)
    principal = _principal(full_config, config_path)
    repository, where = _repository(full_config, db)
    # No event bus: a decision publishes nothing where it is made. The gateway
    # holding the call publishes the granted or denied event when it reads the
    # record, exactly as it does for a REST decision.
    service = ApprovalGateService(repository, ApprovalHoldRegistry(), None, NoOpApprovalDelivery())
    handler = ResolveApprovalHandler(service, auth_components=_DeclaredRoles(principal))

    async def run() -> Any:
        full_id = await _match(repository, principal, approval_id)
        command = ResolveApprovalCommand(full_id, approved, principal, reason)
        return full_id, await handler.handle(command)

    try:
        full_id, result = asyncio.run(run())
    except AccessDeniedError as exc:
        roles = ", ".join(principal.metadata.get("roles") or []) or "none"
        raise CLIError(
            f"{principal.id.value} may not resolve approvals: its declared roles ({roles}) do not hold "
            "approval:resolve.",
            suggestions=[f"Add provider-admin (or admin) to {_ROLES_KEY} in {config_path}."],
        ) from exc

    decision = "approved" if approved else "denied"
    if result.outcome is ResolveOutcome.RESOLVED:
        console.print(f"{full_id} {result.state or decision} by {principal.id.value} ({where}).")
        return
    messages = {
        ResolveOutcome.NOT_FOUND: f"No approval {approval_id} in {where}.",
        ResolveOutcome.EXPIRED: f"{full_id} expired before it was decided; nothing was {decision}.",
        ResolveOutcome.CANCELLED: f"{full_id} was cancelled: the held call will not run.",
        ResolveOutcome.ALREADY_TERMINAL: f"{full_id} is already {result.state}.",
    }
    raise CLIError(messages[result.outcome])


_ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Path to the gateway's config.yaml.")]
_DbOpt = Annotated[Path | None, typer.Option("--db", help="Use this SQLite approval store instead of the config's.")]
_ReasonOpt = Annotated[str | None, typer.Option("--reason", "-r", help="Why, recorded with the decision.")]
_IdArg = Annotated[str, typer.Argument(help="The approval id (a unique prefix of a pending one is enough).")]


def _opts(ctx: typer.Context) -> GlobalOptions:
    return ctx.obj if ctx.obj else GlobalOptions()


@app.command(name="list")
def list_command(
    ctx: typer.Context,
    config: _ConfigOpt = None,
    db: _DbOpt = None,
    state: Annotated[str, typer.Option("--state", help="pending, approved, denied, expired or cancelled.")] = "pending",
) -> None:
    """List approvals in the gateway's approval store (pending by default)."""
    _list(_opts(ctx), config, db, state)


@app.command(name="approve")
def approve_command(
    ctx: typer.Context, approval_id: _IdArg, config: _ConfigOpt = None, db: _DbOpt = None, reason: _ReasonOpt = None
) -> None:
    """Approve a held call as the declared auth.stdio.principal; the gateway releases it within a poll."""
    _resolve(_opts(ctx), config, db, approval_id, True, reason)


@app.command(name="deny")
def deny_command(
    ctx: typer.Context, approval_id: _IdArg, config: _ConfigOpt = None, db: _DbOpt = None, reason: _ReasonOpt = None
) -> None:
    """Deny a held call as the declared auth.stdio.principal."""
    _resolve(_opts(ctx), config, db, approval_id, False, reason)


__all__ = ["app"]
