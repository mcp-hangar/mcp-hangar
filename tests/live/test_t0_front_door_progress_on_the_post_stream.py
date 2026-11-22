"""Tier 0 live verification: a stateless upstream's progress reaches its front-door caller (#2314).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door`` and API-key auth, over the shipped
streamable-HTTP ``/mcp`` surface. Its one upstream is a real SDK server over
HTTP (``_progress_server.py``, stateless, 2026-07-28, SSE framed) whose tool
reports three steps of progress half a second apart. A stateless upstream has
no standing GET stream, so it sends that progress on the body of the POST
response, the only channel it has.

- A caller that attached a ``progressToken`` gets the three progress
  notifications with its own token, in order, before the result, and the
  first one arrives while the tool is still running, not with the result.
  Before #2314 it got the result and no progress at all.
- With the progress sent back to back, right before the result, every call
  still gets all three, then the result: none is lost to the result.
- A caller that attached none gets the result and no progress.

Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_front_door_progress_on_the_post_stream.py -m "live and t0"
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

_TENANT = "tenant-progress"
_TOOL = "count_slowly"
_QUICK_TOOL = "count_quickly"
_PROGRESS_SERVER = Path(__file__).with_name("_progress_server.py")
_MODERN = "2026-07-28"
_CALLER_TOKEN = "caller-progress-1"

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
  progress:
    mode: remote
    endpoint: "{endpoint}"
    tool_projection:
      flat: [{tool}, {quick_tool}]
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
        [sys.executable, str(_PROGRESS_SERVER), str(port)],
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
            pytest.skip("the progress upstream never came up")
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory, upstream_endpoint: str) -> Iterator[_FrontDoor]:
    workdir = tmp_path_factory.mktemp("front_door_progress_on_the_post_stream")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config = _CONFIG.format(
        auth_db=str(auth_db), tenant=_TENANT, endpoint=upstream_endpoint, tool=_TOOL, quick_tool=_QUICK_TOOL
    )
    with running_hangar(workdir, config) as hangar:
        door = _FrontDoor(base_url=hangar.base_url, api_key=keys[_TENANT])
        _wait_for_the_tool(door)
        yield door


def _post(door: _FrontDoor, method: str, params: dict[str, Any]) -> httpx.Response:
    return httpx.post(f"{door.base_url}/mcp", **_request(door, method, params), timeout=60)


def _request(door: _FrontDoor, method: str, params: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": _MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "progress-probe", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    if token is not None:
        meta["progressToken"] = token
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": method,
        "X-API-Key": door.api_key,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    body = {"jsonrpc": "2.0", "id": 51, "method": method, "params": {**params, "_meta": meta}}
    return {"headers": headers, "content": json.dumps(body)}


def _payload(response: httpx.Response) -> dict[str, Any]:
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return dict(json.loads(text))


def _wait_for_the_tool(door: _FrontDoor) -> None:
    """The front door warms its upstream after boot; wait until the caller's listing holds the tool."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        answer = _payload(_post(door, "tools/list", {}))
        if _TOOL in [tool["name"] for tool in answer.get("result", {}).get("tools", [])]:
            return
        time.sleep(0.5)
    pytest.fail(f"{_TOOL} never reached the front door's listing")


def _call(door: _FrontDoor, token: str | None, tool: str = _TOOL) -> list[tuple[float, dict[str, Any]]]:
    """Each message of the call's response, with when it arrived, in order."""
    messages: list[tuple[float, dict[str, Any]]] = []
    request = _request(door, "tools/call", {"name": tool, "arguments": {}}, token)
    with httpx.stream("POST", f"{door.base_url}/mcp", timeout=60, **request) as response:
        assert response.status_code == 200, response.read()[:300]
        if "text/event-stream" not in response.headers.get("Content-Type", ""):
            return [(time.monotonic(), dict(json.loads(response.read())))]
        for line in response.iter_lines():
            if line.startswith("data:") and line[5:].strip():
                messages.append((time.monotonic(), dict(json.loads(line[5:].strip()))))
    return messages


def test_the_caller_gets_its_progress_in_order_as_it_happens_then_the_result(front_door: _FrontDoor) -> None:
    messages = _call(front_door, _CALLER_TOKEN)

    progress = [(at, m["params"]) for at, m in messages if m.get("method") == "notifications/progress"]
    answers = [(at, m) for at, m in messages if "result" in m or "error" in m]
    assert [p["progress"] for _, p in progress] == [1, 2, 3], messages
    assert all(p["progressToken"] == _CALLER_TOKEN for _, p in progress), messages
    assert [p.get("total") for _, p in progress] == [3, 3, 3], messages
    ((answered_at, answer),) = answers
    assert answer["id"] == 51
    assert answer["result"]["content"][0]["text"] == "counted", answer
    assert messages[-1][1] is answer, "the result is the last message of the call"
    # The tool pauses half a second after each step: progress relayed as it
    # arrives is a second or more ahead of the result, buffered it is not.
    assert answered_at - progress[0][0] >= 0.8, messages


def test_progress_sent_right_before_the_result_is_not_lost_to_it(front_door: _FrontDoor) -> None:
    for attempt in range(20):
        messages = [m for _, m in _call(front_door, f"{_CALLER_TOKEN}-{attempt}", _QUICK_TOOL)]

        progress = [m["params"]["progress"] for m in messages if m.get("method") == "notifications/progress"]
        assert progress == [1, 2, 3], (attempt, messages)
        assert messages[-1]["result"]["content"][0]["text"] == "counted", (attempt, messages)


def test_a_caller_that_asked_for_no_progress_gets_none(front_door: _FrontDoor) -> None:
    messages = _call(front_door, None)

    assert [m.get("method") for _, m in messages if "method" in m] == [], messages
    (answer,) = [m for _, m in messages if "result" in m]
    assert answer["result"]["content"][0]["text"] == "counted", answer
