"""Tier 0 live verification: an HTTP client that gives up on a held call cancels its approval (#2289).

The streamable HTTP counterpart of ``test_t0_stdio_client_cancel_withdraws_hold.py``
(#2279). The gateway serves HTTP without a session (#877), so the client's cancel
takes a different way on each era:

- handshake era (here 2025-11-25): the SDK client POSTs ``notifications/cancelled``
  as a request of its own. The server built for that POST had no request with
  the id and dropped it, so the hold went on and a later approval ran a call
  the client had been told failed. The gateway now finds the held call through
  its approval record and cancels it;
- 2026-07-28: the client closes the request's stream, the SDK cancels the tool
  handler, and the #2279 path applies.

A cancel is the caller's: one that another tenant posts with the same request
id leaves the hold alone, and the call runs once its approval comes.

BLACK-BOX against a real ``mcp-hangar serve --http``. The upstream appends a
line to a file each time a tool runs, and the approver is the CLI in another
process. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_http_client_cancel_withdraws_hold.py \\
        -m "live and t0" -o addopts="" -n auto --timeout=180
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession

from tests.live import _group_support as gs
from tests.live.conftest import running_hangar
from tests.live.test_t0_stdio_client_cancel_withdraws_hold import (
    _UPSTREAM,
    _assert_withdrawn,
    _cli,
    _give_up_then_approve,
    _held,
    _ran,
    _rows,
)

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant-gives-up"
_OTHER = "tenant-next-door"

_HTTP = """\
logging:
  level: WARNING
mcp_servers:
  demo:
    mode: subprocess
    command: ["{python}", "{upstream}", "{runs}"]
    idle_ttl_s: 120
    tools:
      approval_list: [send_message, send_slowly]
      approval_timeout_seconds: 60
    tool_projection:
      flat: [send_message, send_slowly]
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: "{auth_db}"
  role_assignments:
    - principal: "svc:{tenant}"
      role: developer
      scope: global
  stdio:
    principal:
      id: local-approver
      tenant_id: {tenant}
      roles: [provider-admin]
event_store:
  driver: sqlite
  path: "{state}/events.db"
"""


@contextlib.contextmanager
def _gateway(tmp_path: Path) -> Iterator[tuple[Any, dict[str, str], Path, Path]]:
    auth_db = tmp_path / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT, _OTHER])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")
    upstream = tmp_path / "counted.py"
    upstream.write_text(_UPSTREAM.replace("{slow}", "8.0"))
    runs = tmp_path / "runs.txt"
    config = tmp_path / "config.yaml"
    config.write_text(
        _HTTP.format(
            python=sys.executable, upstream=upstream, runs=runs, auth_db=auth_db, tenant=_TENANT, state=tmp_path
        )
    )
    with running_hangar(tmp_path, config.read_text()) as hangar:
        yield hangar, keys, config, runs


@contextlib.asynccontextmanager
async def _session(hangar: Any, key: str) -> AsyncIterator[ClientSession]:
    from tests.live._mcp_client import open_mcp_streams

    async with open_mcp_streams(f"{hangar.base_url}/mcp", {"X-API-Key": key}) as (read, write):
        async with ClientSession(read, write) as session:
            yield session


@pytest.mark.parametrize("era", ["handshake", "2026-07-28"])
def test_an_http_front_door_call_the_client_gave_up_on_is_cancelled_and_never_runs(tmp_path: Path, era: str) -> None:
    with _gateway(tmp_path) as (hangar, keys, config, runs):

        async def run() -> dict[str, Any]:
            async with _session(hangar, keys[_TENANT]) as session:
                if era != "handshake":
                    await session.discover()
                return await _give_up_then_approve(session, config, front_door=True, connect=era == "handshake")

        outcome = asyncio.run(asyncio.wait_for(run(), timeout=120))

    _assert_withdrawn(outcome, runs)


def test_an_http_call_approved_before_the_client_gave_up_runs_once(tmp_path: Path) -> None:
    """Approved first; the client then cancels while the upstream runs the call. It runs, once."""
    from tests.live.test_t0_stdio_client_cancel_withdraws_hold import _call

    with _gateway(tmp_path) as (hangar, keys, config, runs):

        async def run() -> tuple[Any, list[dict[str, Any]]]:
            async with _session(hangar, keys[_TENANT]) as session:
                await session.initialize()
                call = asyncio.create_task(_call(session, True, "send_slowly", "approved in time", None))
                approval_id = await _held(config)
                approved = await asyncio.to_thread(_cli, "approvals", "approve", approval_id, "--config", str(config))
                for _ in range(200):
                    if _ran(runs) or call.done():
                        break
                    await asyncio.sleep(0.1)
                call.cancel()  # the SDK POSTs notifications/cancelled for the running call
                with contextlib.suppress(asyncio.CancelledError):
                    await call
                await asyncio.sleep(8.0)
                return approved, await asyncio.to_thread(_rows, config, "approved")

        approved, approved_rows = asyncio.run(asyncio.wait_for(run(), timeout=120))

    said = " ".join((approved.stdout + approved.stderr).split())
    assert approved.returncode == 0, said
    assert len(approved_rows) == 1, approved_rows
    assert _ran(runs) == ["approved in time"], _ran(runs)


def test_a_cancel_another_tenant_posts_leaves_the_hold_alone(tmp_path: Path) -> None:
    """Another tenant cancels every request id the caller could be using; the call stays held and then runs."""
    from tests.live.test_t0_stdio_client_cancel_withdraws_hold import _call

    with _gateway(tmp_path) as (hangar, keys, config, runs):

        async def run() -> tuple[list[dict[str, Any]], Any]:
            async with _session(hangar, keys[_TENANT]) as session:
                await session.initialize()
                call = asyncio.create_task(_call(session, True, "send_message", "still mine to send", None))
                approval_id = await _held(config)
                async with httpx.AsyncClient(base_url=hangar.base_url, timeout=10) as other:
                    for request_id in [*range(10), *(str(i) for i in range(10))]:
                        posted = await other.post(
                            "/mcp",
                            json={
                                "jsonrpc": "2.0",
                                "method": "notifications/cancelled",
                                "params": {"requestId": request_id},
                            },
                            headers={
                                "X-API-Key": keys[_OTHER],
                                "Accept": "application/json, text/event-stream",
                                "MCP-Protocol-Version": "2025-11-25",
                            },
                        )
                        assert posted.status_code == 202, posted.text
                await asyncio.sleep(3)
                pending = await asyncio.to_thread(_rows, config, "pending")
                approved = await asyncio.to_thread(_cli, "approvals", "approve", approval_id, "--config", str(config))
                await asyncio.wait_for(call, timeout=30)
                return pending, approved

        pending, approved = asyncio.run(asyncio.wait_for(run(), timeout=120))

    assert len(pending) == 1, pending
    said = " ".join((approved.stdout + approved.stderr).split())
    assert approved.returncode == 0, said
    assert _ran(runs) == ["still mine to send"], _ran(runs)
