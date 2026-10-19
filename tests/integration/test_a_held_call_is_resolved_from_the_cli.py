"""A call held by a stdio gateway is resolved by `mcp-hangar approvals` in another process (#1778).

A stdio gateway serves no REST API, so nothing could resolve a call on an
``approval_list`` and it ended in a timeout. The maintainer's decision
(2026-10-19): the waiting gateway learns of the decision by polling the approval
store it already shares, and no notification path is added. This proves that
with two real processes on one SQLite file:

* process A is ``mcp-hangar serve`` over stdio, driven by the SDK's own client,
  holding a call to ``echo``;
* process B is ``mcp-hangar approvals approve|deny``, run as a user would;
* A returns the upstream's result (or the denial), and its event store records
  the decision with ``decided_by`` = the declared stdio principal.

And the decided rule for who may resolve: the declared roles decide even with
auth off, so ``viewer`` -- what ``init`` declares -- is refused and the call
stays held.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._hangar_executable import hangar_executable

UPSTREAM = str(Path(__file__).resolve().parent.parent.parent / "examples" / "rugpull" / "server.py")

DRIVER = """\
import json, sys, time

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.argv[1], args=["--config", sys.argv[2], "serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for _ in range(60):
                if "echo" in {t.name for t in (await session.list_tools()).tools}:
                    break
                await anyio.sleep(0.5)
            started = time.monotonic()
            result = await session.call_tool("echo", {"text": "released"})
            dumped = result.model_dump(mode="json")
            print(json.dumps({
                "is_error": bool(dumped.get("isError") or dumped.get("is_error")),
                "text": " ".join(b.get("text", "") for b in dumped.get("content") or []),
                "held_s": time.monotonic() - started,
            }))


anyio.run(main)
"""

CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{upstream}"]
    idle_ttl_s: 60
    tools:
      approval_list: [echo]
      approval_timeout_seconds: 40
tool_access:
  mode: front_door
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [{role}]
event_store:
  driver: sqlite
  path: "{state}/events.db"
"""


def _config(tmp_path: Path, role: str) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(python=sys.executable, upstream=UPSTREAM, role=role, state=state))
    return config


def _cli(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # The CLI is run from a directory of its own, to prove it finds the store
    # through the configuration and not through a shared working directory.
    cwd = config.parent / "elsewhere"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [hangar_executable(), *args, "--config", str(config)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd),
        env={**os.environ, "COLUMNS": "200"},
    )


def _held(config: Path, deadline_s: float = 30.0) -> str:
    """The id of the one pending approval, as `approvals list` reports it."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        listed = subprocess.run(
            [hangar_executable(), "--json", "approvals", "list", "--config", str(config)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if listed.returncode == 0 and listed.stdout.strip():
            rows = json.loads(listed.stdout.strip().splitlines()[-1])
            if rows:
                [row] = rows
                assert row["tool"] == "echo" and row["requested_by"] == "local-user", row
                return str(row["approval_id"])
        time.sleep(0.5)
    raise AssertionError(f"no call was held: {listed.stdout}\n{listed.stderr}")


def _gateway(tmp_path: Path, config: Path) -> subprocess.Popen[str]:
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    return subprocess.Popen(
        [sys.executable, str(driver), hangar_executable(), str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(tmp_path),
    )


def _answer(gateway: subprocess.Popen[str]) -> dict:
    out, err = gateway.communicate(timeout=50)
    assert gateway.returncode == 0, f"the client failed:\n{out}\n{err}"
    return json.loads(out.strip().splitlines()[-1])


def _decisions(config: Path) -> list[tuple[str, dict]]:
    with sqlite3.connect(config.parent / "state" / "events.db") as conn:
        rows = conn.execute(
            "SELECT event_type, data FROM events WHERE event_type IN ('ToolApprovalGranted', 'ToolApprovalDenied')"
        ).fetchall()
    return [(kind, json.loads(data)) for kind, data in rows]


@pytest.mark.parametrize("role", ["provider-admin", "admin"])
def test_an_approval_from_the_cli_releases_the_held_call(tmp_path: Path, role: str) -> None:
    config = _config(tmp_path, role)
    gateway = _gateway(tmp_path, config)
    try:
        approval_id = _held(config)
        # A prefix is enough, as it is for `why`.
        approved = _cli(config, "approvals", "approve", approval_id[:8])
        assert approved.returncode == 0, approved.stdout + approved.stderr
        assert "approved by local-user" in approved.stdout, approved.stdout
        answer = _answer(gateway)
    finally:
        gateway.kill()

    assert answer["is_error"] is False, answer
    assert answer["text"] == "released", answer
    # Audited by the gateway that held the call, as a REST decision is.
    [(kind, event)] = _decisions(config)
    assert kind == "ToolApprovalGranted"
    assert event["decided_by"] == "local-user"
    assert event["approval_id"] == approval_id


def test_a_denial_from_the_cli_refuses_the_held_call(tmp_path: Path) -> None:
    config = _config(tmp_path, "provider-admin")
    gateway = _gateway(tmp_path, config)
    try:
        approval_id = _held(config)
        denied = _cli(config, "approvals", "deny", approval_id, "--reason", "not today")
        assert denied.returncode == 0, denied.stdout + denied.stderr
        answer = _answer(gateway)
    finally:
        gateway.kill()

    assert answer["is_error"] is True, answer
    assert "released" not in answer["text"], answer
    [(kind, event)] = _decisions(config)
    assert kind == "ToolApprovalDenied"
    assert event["decided_by"] == "local-user"
    assert event["reason"] == "not today"


def test_a_viewer_cannot_resolve_and_the_call_stays_held(tmp_path: Path) -> None:
    config = _config(tmp_path, "viewer")
    gateway = _gateway(tmp_path, config)
    try:
        approval_id = _held(config)
        refused = _cli(config, "approvals", "approve", approval_id)
        still = subprocess.run(
            [hangar_executable(), "--json", "approvals", "list", "--config", str(config)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        gateway.kill()

    assert refused.returncode == 1, refused.stdout + refused.stderr
    message = " ".join((refused.stdout + refused.stderr).split())
    assert "do not hold approval:resolve" in message, message
    assert "provider-admin" in message and "auth.stdio.principal.roles" in message, message
    [row] = json.loads(still.stdout.strip().splitlines()[-1])
    assert row["approval_id"] == approval_id and row["state"] == "pending" and row["decided_by"] is None
