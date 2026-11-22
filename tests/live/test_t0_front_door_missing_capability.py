"""Tier 0 live verification: an upstream's -32021 reaches a front-door caller as its protocol error (#2315).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door`` and API-key auth, over the shipped
streamable-HTTP ``/mcp`` surface. Its one upstream is a real SDK server over
HTTP (``_capability_server.py``, stateless, 2026-07-28) whose tool answers
``-32021`` naming ``{"sampling": {}}`` on HTTP 400 when sampling was not
declared. Hangar declares no sampling upstream, so the upstream always says so.

- A 2026-07-28 caller that declared nothing gets HTTP 400 and the ``-32021``,
  with ``data.requiredCapabilities`` naming sampling and its own request id.
  Before #2315 it got a tool result, ``isError: true``, text
  ``tool_error: HTTP error: 400``, on HTTP 200.
- A caller that declared sampling keeps the tool error: telling it to declare
  what it declared would be false.

Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_front_door_missing_capability.py -m "live and t0"
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

_TENANT = "tenant-capability"
_TOOL = "needs_sampling"
_CAPABILITY_SERVER = Path(__file__).with_name("_capability_server.py")
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
  capability:
    mode: remote
    endpoint: "{endpoint}"
    tool_projection:
      flat: [{tool}]
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
        [sys.executable, str(_CAPABILITY_SERVER), str(port)],
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
            pytest.skip("the capability upstream never came up")
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory, upstream_endpoint: str) -> Iterator[_FrontDoor]:
    workdir = tmp_path_factory.mktemp("front_door_missing_capability")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config = _CONFIG.format(auth_db=str(auth_db), tenant=_TENANT, endpoint=upstream_endpoint, tool=_TOOL)
    with running_hangar(workdir, config) as hangar:
        door = _FrontDoor(base_url=hangar.base_url, api_key=keys[_TENANT])
        _wait_for_the_tool(door)
        yield door


def _post(door: _FrontDoor, method: str, params: dict[str, Any], capabilities: dict[str, Any]) -> httpx.Response:
    meta = {
        "io.modelcontextprotocol/protocolVersion": _MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "missing-capability-probe", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": capabilities,
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
    body = {"jsonrpc": "2.0", "id": 41, "method": method, "params": {**params, "_meta": meta}}
    return httpx.post(f"{door.base_url}/mcp", headers=headers, content=json.dumps(body), timeout=60)


def _payload(response: httpx.Response) -> dict[str, Any]:
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return dict(json.loads(text))


def _wait_for_the_tool(door: _FrontDoor) -> None:
    """The front door warms its upstream after boot; wait until the caller's listing holds the tool."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        answer = _payload(_post(door, "tools/list", {}, {}))
        if _TOOL in [tool["name"] for tool in answer.get("result", {}).get("tools", [])]:
            return
        time.sleep(0.5)
    pytest.fail(f"{_TOOL} never reached the front door's listing")


def test_a_caller_that_declared_nothing_gets_the_minus_32021_on_a_400(front_door: _FrontDoor) -> None:
    response = _post(front_door, "tools/call", {"name": _TOOL, "arguments": {}}, {})

    assert response.status_code == 400, response.text[:300]
    payload = _payload(response)
    assert payload["id"] == 41
    assert payload["error"]["code"] == -32021, payload
    assert payload["error"]["data"] == {"requiredCapabilities": {"sampling": {}}}, payload
    assert payload["error"]["message"] == "Client must declare the sampling capability"


def test_a_caller_that_declared_sampling_keeps_the_tool_error(front_door: _FrontDoor) -> None:
    response = _post(front_door, "tools/call", {"name": _TOOL, "arguments": {}}, {"sampling": {}})

    assert response.status_code == 200, response.text[:300]
    payload = _payload(response)
    assert "error" not in payload, payload
    assert payload["result"]["isError"] is True, payload
