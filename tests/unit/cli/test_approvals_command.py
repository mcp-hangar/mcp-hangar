"""`mcp-hangar approvals`: who decides, which store, and the chokepoint's outcomes (#1778).

The two-process proof is in tests/integration/test_a_held_call_is_resolved_from_the_cli.py.
These pin the refusals around it: no declared principal, a store the command
cannot locate, a role without ``approval:resolve`` with auth off, and the
outcomes the resolution chokepoint reports.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.server.cli.errors import CLIError
from mcp_hangar.server.cli.main import app

PRINCIPAL = """\
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [{roles}]
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _config(tmp_path: Path, *, roles: str = "provider-admin", principal: bool = True, absolute: bool = True) -> Path:
    body = "mcp_servers: {}\n"
    if absolute:
        body += f'event_store:\n  driver: sqlite\n  path: "{tmp_path / "events.db"}"\n'
    if principal:
        body += PRINCIPAL.format(roles=roles)
    config = tmp_path / "config.yaml"
    config.write_text(body)
    return config


def _seed(tmp_path: Path, *, tenant: str | None = "local", expired: bool = False, state=ApprovalState.PENDING) -> str:
    now = datetime.now(UTC)
    request = ApprovalRequest(
        approval_id="0f1e2d3c-aaaa-bbbb-cccc-000000000001",
        provider_id="math",
        tool_name="echo",
        arguments={"text": "hi"},
        arguments_hash="h",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1) if expired else now + timedelta(minutes=5),
        state=state,
        channel="event_stream",
        correlation_id="c",
        tenant_id=tenant,
        requested_by="local-user",
    )
    asyncio.run(_repository(tmp_path).save(request))
    return request.approval_id


def _repository(tmp_path: Path) -> SqliteApprovalRepository:
    return SqliteApprovalRepository(Database(DatabaseConfig(path=str(tmp_path / "mcp_hangar.db"))))


def _record(tmp_path: Path, approval_id: str) -> ApprovalRequest:
    record = asyncio.run(_repository(tmp_path).get(approval_id))
    assert record is not None
    return record


def _refusal(result) -> str:
    assert isinstance(result.exception, CLIError), result.output
    error = result.exception
    return " ".join([error.message, error.reason, *error.suggestions])


def test_the_approver_is_the_declared_principal(runner: CliRunner, tmp_path: Path) -> None:
    approval_id = _seed(tmp_path)

    result = runner.invoke(app, ["approvals", "approve", approval_id, "-c", str(_config(tmp_path)), "-r", "ok"])

    assert result.exit_code == 0, result.output
    record = _record(tmp_path, approval_id)
    assert record.state is ApprovalState.APPROVED
    assert record.decided_by == "local-user"
    assert record.reason == "ok"


def test_no_declared_principal_is_refused_not_defaulted(runner: CliRunner, tmp_path: Path) -> None:
    approval_id = _seed(tmp_path)

    result = runner.invoke(app, ["approvals", "approve", approval_id, "-c", str(_config(tmp_path, principal=False))])

    assert "auth.stdio.principal" in _refusal(result)
    assert _record(tmp_path, approval_id).state is ApprovalState.PENDING


@pytest.mark.parametrize("roles", ["viewer", "viewer, auditor", "developer", ""])
def test_a_role_without_approval_resolve_is_refused_with_auth_off(runner: CliRunner, tmp_path: Path, roles) -> None:
    approval_id = _seed(tmp_path)

    result = runner.invoke(app, ["approvals", "deny", approval_id, "-c", str(_config(tmp_path, roles=roles))])

    message = _refusal(result)
    assert "approval:resolve" in message and "provider-admin" in message
    assert "auth.stdio.principal.roles" in message
    assert _record(tmp_path, approval_id).state is ApprovalState.PENDING


def test_a_relative_store_is_not_guessed(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["approvals", "list", "-c", str(_config(tmp_path, absolute=False))])

    assert "working directory" in _refusal(result)


def test_a_missing_store_is_not_created(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["approvals", "list", "-c", str(_config(tmp_path))])

    assert "No approval store" in _refusal(result)
    assert not (tmp_path / "mcp_hangar.db").exists()


def test_db_names_the_store(runner: CliRunner, tmp_path: Path) -> None:
    approval_id = _seed(tmp_path)
    config = _config(tmp_path, absolute=False)

    result = runner.invoke(
        app, ["--json", "approvals", "list", "-c", str(config), "--db", str(tmp_path / "mcp_hangar.db")]
    )

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.output.strip().splitlines()[-1])
    assert row["approval_id"] == approval_id and row["arguments"] == {"text": "hi"}


def test_another_tenants_approval_is_neither_listed_nor_resolvable(runner: CliRunner, tmp_path: Path) -> None:
    approval_id = _seed(tmp_path, tenant="other")
    config = str(_config(tmp_path))

    listed = runner.invoke(app, ["--json", "approvals", "list", "-c", config])
    resolved = runner.invoke(app, ["approvals", "approve", approval_id, "-c", config])

    assert json.loads(listed.output.strip().splitlines()[-1]) == []
    assert "No approval" in _refusal(resolved)
    assert _record(tmp_path, approval_id).state is ApprovalState.PENDING


def test_an_expired_approval_is_expired_not_approved(runner: CliRunner, tmp_path: Path) -> None:
    approval_id = _seed(tmp_path, expired=True)

    result = runner.invoke(app, ["approvals", "approve", approval_id, "-c", str(_config(tmp_path))])

    assert "expired" in _refusal(result)
    record = _record(tmp_path, approval_id)
    assert record.state is ApprovalState.EXPIRED and record.decided_by is None


def test_a_decided_approval_is_not_decided_again(runner: CliRunner, tmp_path: Path) -> None:
    approval_id = _seed(tmp_path, state=ApprovalState.DENIED)

    result = runner.invoke(app, ["approvals", "approve", approval_id, "-c", str(_config(tmp_path))])

    assert "already denied" in _refusal(result)
    assert _record(tmp_path, approval_id).state is ApprovalState.DENIED


def test_an_unknown_state_is_refused(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["approvals", "list", "-c", str(_config(tmp_path)), "--state", "maybe"])

    assert "Unknown state" in _refusal(result)
