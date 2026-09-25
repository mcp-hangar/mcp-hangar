"""Tier 0 live verification: an HTTP client connected before the fleet is warm is told to re-list (#1366).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door``. The one upstream waits
:data:`_UPSTREAM_DELAY_S` before it reads its handshake, longer than #1231's
5 s wait on a first empty listing, so the test cannot pass on that wait. The
client does what the TypeScript SDK client does over the handshake era:
``initialize``, ``notifications/initialized``, then a sessionless ``GET /mcp``,
then ``tools/list``. It never reconnects.

(The Python SDK 2.0.0 client opens that GET only when it holds a session id,
which a stateless front door never issues, so it is not the client here.)

Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_http_tools_list_changed.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant-early"
_HANDSHAKE = "2025-11-25"
#: Past #1231's 5 s wait, inside the stdio client's 15 s handshake timeout.
_UPSTREAM_DELAY_S = 10
_LIST_CHANGED = "notifications/tools/list_changed"

_CONFIG = """\
logging:
  level: WARNING
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
    path: {auth_db}
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{slow_start}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 120
"""

#: The stub backend, started late. A file rather than ``python -c``: the launcher refuses shell metacharacters.
_SLOW_START = """\
import runpy
import sys
import time

time.sleep({delay})
sys.argv = [{server!r}]
runpy.run_path({server!r}, run_name="__main__")
"""


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("http_list_changed")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    slow_start = workdir / "slow_math.py"
    slow_start.write_text(_SLOW_START.format(delay=_UPSTREAM_DELAY_S, server=str(_MATH_SERVER)))
    config = _CONFIG.format(auth_db=str(auth_db), python=sys.executable, slow_start=str(slow_start))
    with running_hangar(workdir, config) as hangar:
        yield hangar.base_url, keys[_TENANT]


def _payload(response: httpx.Response) -> dict[str, Any]:
    text = response.text.lstrip()
    if text.startswith("{"):
        return dict(json.loads(text))
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[len("data: ") :]))
    raise AssertionError(f"neither JSON nor an SSE data frame: {text[:200]!r}")


def test_a_client_connected_before_the_upstream_is_warm_gets_its_tools_without_reconnecting(
    gateway: tuple[str, str],
) -> None:
    base_url, api_key = gateway
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _HANDSHAKE,
        "X-API-Key": api_key,
    }
    frames: queue.Queue[str] = queue.Queue()

    with httpx.Client(base_url=base_url, headers=headers, timeout=httpx.Timeout(30.0, read=None)) as client:

        def post(method: str, request_id: int | None = None) -> httpx.Response:
            body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": {}}
            if method == "initialize":
                body["params"] = {
                    "protocolVersion": _HANDSHAKE,
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                }
            if request_id is not None:
                body["id"] = request_id
            return client.post("/mcp", content=json.dumps(body))

        def listed() -> list[str]:
            return sorted(tool["name"] for tool in _payload(post("tools/list", 2))["result"]["tools"])

        initialized = _payload(post("initialize", 1))
        assert initialized["result"]["capabilities"]["tools"].get("listChanged") is True, initialized
        assert post("notifications/initialized").status_code == 202

        def read() -> None:
            with client.stream("GET", "/mcp", headers={"Accept": "text/event-stream"}) as response:
                frames.put(f"status {response.status_code}")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        frames.put(line[len("data: ") :])

        threading.Thread(target=read, daemon=True).start()
        assert frames.get(timeout=10) == "status 200"
        assert _LIST_CHANGED in frames.get(timeout=10)  # the one sent as the stream opens

        first = listed()
        assert "add" not in first, f"the upstream was warm before the first listing; the test proves nothing: {first}"

        # The catalogue lands after the upstream's delay; the push must follow it.
        assert _LIST_CHANGED in frames.get(timeout=_UPSTREAM_DELAY_S + 20)

        assert {"add", "subtract", "multiply", "divide"} <= set(listed())


def test_an_open_stream_does_not_hold_the_gateway_up_after_sigterm(tmp_path_factory: pytest.TempPathFactory) -> None:
    """A stream held open by a client must not keep a stopping gateway alive.

    A hand-rolled stream once did: uvicorn waits for open responses, and only
    sse-starlette's response ends its stream when the server is told to stop.
    """
    workdir = tmp_path_factory.mktemp("http_list_changed_stop")
    config = "logging:\n  level: WARNING\ntool_access:\n  mode: front_door\nmcp_servers: {}\n"
    with running_hangar(workdir, config) as hangar:
        opened: queue.Queue[int] = queue.Queue()

        def hold() -> None:
            try:
                with httpx.stream(
                    "GET",
                    f"{hangar.base_url}/mcp",
                    headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(10.0, read=None),
                ) as response:
                    opened.put(response.status_code)
                    for _ in response.iter_lines():
                        pass
            except httpx.HTTPError:
                pass

        threading.Thread(target=hold, daemon=True).start()
        assert opened.get(timeout=10) == 200

        hangar.proc.terminate()
        hangar.proc.wait(timeout=10)  # raises TimeoutExpired if the stream holds it up
