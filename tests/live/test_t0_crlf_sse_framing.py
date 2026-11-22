"""Tier 0 live verification: a session-based Python-SDK upstream's CRLF SSE events reach the caller (#2327).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door`` and API-key auth, over the shipped
streamable-HTTP ``/mcp`` surface. Its one upstream is a real Python MCP SDK
server over HTTP (``_session_sse_server.py``, ``stateless_http=False``,
``json_response=False``, with an ``EventStore``), so sse-starlette frames every
SSE event with CRLF on both the standing GET stream and a resumed GET stream.

- Standing GET stream: calling ``announce_change`` registers ``late_tool`` on
  the upstream and sends ``notifications/tools/list_changed`` on the standing
  GET stream. Hangar must split the CRLF event, re-discover the upstream's
  tools, and expose ``late_tool`` in ``tools/list``.
- Resume path (SEP-1699): calling ``resumed_answer`` closes the POST stream
  after a CRLF priming event and delivers the JSON-RPC response on the resumed
  GET stream. Hangar must split both and return ``resumed-ok`` to the caller.

Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_crlf_sse_framing.py -m "live and t0"
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _free_port, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant-crlf-sse"
_UPSTREAM_SERVER = Path(__file__).with_name("_session_sse_server.py")
_MODERN = "2026-07-28"

_CONFIG = """\
logging:
  level: INFO
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: true
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "svc:{tenant}"
      role: developer
      scope: global
mcp_servers:
  session_sse:
    mode: remote
    endpoint: "{endpoint}"
    http:
      read_timeout: 6
    tool_projection:
      flat: [announce_change, late_tool, resumed_answer]
"""


@dataclass
class _FrontDoor:
    base_url: str
    api_key: str


@pytest.fixture(scope="module")
def upstream_endpoint() -> Iterator[str]:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    process = subprocess.Popen(  # noqa: S603 -- this checkout's stub, this interpreter
        [sys.executable, str(_UPSTREAM_SERVER), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.skip("the session SSE upstream never came up")
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory, upstream_endpoint: str) -> Iterator[_FrontDoor]:
    workdir = tmp_path_factory.mktemp("front_door_crlf_sse_framing")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config = _CONFIG.format(auth_db=str(auth_db), tenant=_TENANT, endpoint=upstream_endpoint)
    with running_hangar(workdir, config) as hangar:
        door = _FrontDoor(base_url=hangar.base_url, api_key=keys[_TENANT])
        _wait_for_tool(door, "announce_change", timeout_s=60)
        yield door


def _post(door: _FrontDoor, method: str, params: dict[str, Any], *, timeout: float = 15) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": _MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "crlf-probe", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": method,
        "X-API-Key": door.api_key,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    body = {"jsonrpc": "2.0", "id": 71, "method": method, "params": {**params, "_meta": meta}}
    response = httpx.post(f"{door.base_url}/mcp", headers=headers, content=json.dumps(body), timeout=timeout)
    assert response.status_code == 200, response.text[:300]
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return dict(json.loads(text))


def _listed_tools(door: _FrontDoor) -> list[str]:
    answer = _post(door, "tools/list", {})
    return [tool["name"] for tool in answer.get("result", {}).get("tools", [])]


def _wait_for_tool(door: _FrontDoor, name: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if name in _listed_tools(door):
            return
        time.sleep(0.3)
    pytest.fail(f"{name} never reached the front door's listing (saw {_listed_tools(door)})")


def test_crlf_tools_list_changed_on_the_standing_get_stream_updates_the_listing(front_door: _FrontDoor) -> None:
    assert "late_tool" not in _listed_tools(front_door)

    answer = _post(front_door, "tools/call", {"name": "announce_change", "arguments": {}})
    assert answer["result"]["content"][0]["text"] == "announced", answer

    _wait_for_tool(front_door, "late_tool", timeout_s=10)


def test_crlf_early_closed_post_stream_resumes_and_returns_the_answer(front_door: _FrontDoor) -> None:
    answer = _post(front_door, "tools/call", {"name": "resumed_answer", "arguments": {}}, timeout=15)

    assert "result" in answer, answer
    assert answer["result"]["content"][0]["text"] == "resumed-ok", answer
