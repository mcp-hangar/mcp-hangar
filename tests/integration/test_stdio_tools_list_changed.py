"""A stdio client hears the catalogue land, and hears it change, without reconnecting (#1366).

``mcp-hangar serve`` in a subprocess, front_door over stdio, driven by the SDK's
own ``ClientSession`` at the handshake era. The upstream is a stdio process that
takes about 8 seconds to answer ``initialize``: longer than #1231's 5 s wait, so
the first listing is served without its tools and only the notification can put
them in front of the client. It also has a tool that grows the catalogue and
announces it with its own ``notifications/tools/list_changed`` on the pipe, which
stdio upstreams used to drop.

The driver runs in its own process for the reason ``test_a_client_over_stdio_gets_a_verdict``
gives: the SDK's stdio client owns the gateway subprocess's lifetime.

Naming: neutral placeholders only (read_item, grow_item, late_item).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests._hangar_executable import hangar_executable

HANDSHAKE_DELAY_S = 8.0

UPSTREAM = """\
import json, sys, time

DELAY = float(sys.argv[1])
tools = ["read_item", "grow_item"]


def send(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    request = json.loads(line)
    method, rid = request.get("method"), request.get("id")
    if rid is None:
        continue
    if method == "initialize":
        time.sleep(DELAY)
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "slow-upstream", "version": "0"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in tools]}
    elif method == "tools/call":
        name = request["params"]["name"]
        if name == "grow_item" and "late_item" not in tools:
            tools.append("late_item")
        result = {"content": [{"type": "text", "text": "ok"}]}
        send({"jsonrpc": "2.0", "id": rid, "result": result})
        if name == "grow_item":
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        continue
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": method}})
        continue
    send({"jsonrpc": "2.0", "id": rid, "result": result})
"""

DRIVER = """\
import json, sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BINARY, CONFIG = sys.argv[1], sys.argv[2]
changed = anyio.Event()
heard = []


async def on_message(message):
    method = getattr(getattr(message, "root", message), "method", None)
    if method == "notifications/tools/list_changed":
        heard.append(method)
        changed.set()


async def names(session):
    return sorted(t.name for t in (await session.list_tools()).tools)


async def main():
    global changed
    report = {}
    params = StdioServerParameters(command=BINARY, args=["--config", CONFIG, "serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, message_handler=on_message) as session:
            init = await session.initialize()
            report["protocol"] = init.protocol_version
            report["list_changed"] = bool(init.capabilities.tools and init.capabilities.tools.list_changed)
            report["first"] = await names(session)
            with anyio.fail_after(20):
                await changed.wait()
            report["after_landing"] = await names(session)

            changed = anyio.Event()
            await session.call_tool("grow_item", {})
            with anyio.fail_after(10):
                await changed.wait()
            report["after_growth"] = await names(session)
            report["heard"] = len(heard)
    print(json.dumps(report))


anyio.run(main)
"""

CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  shelf:
    mode: subprocess
    command: ["{python}", "{upstream}", "{delay}"]
    idle_ttl_s: 120
tool_access:
  mode: front_door
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [viewer]
"""


def test_a_stdio_client_is_told_when_a_slow_catalogue_lands_and_when_it_grows(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream.py"
    upstream.write_text(UPSTREAM)
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(python=sys.executable, upstream=upstream, delay=HANDSHAKE_DELAY_S))
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)

    result = subprocess.run(
        [sys.executable, str(driver), hangar_executable(), str(config)],
        capture_output=True,
        text=True,
        timeout=50,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"the client failed:\n{result.stdout}\n{result.stderr[-4000:]}"
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert report["protocol"] == "2025-11-25"
    assert report["list_changed"] is True, report
    # Past #1231's wait and still before the upstream answered: no upstream tools.
    assert "read_item" not in report["first"], report
    # The notification arrived, and the same session's re-list has them.
    assert {"read_item", "grow_item"} <= set(report["after_landing"]), report
    # The upstream's own list_changed, on its stdio pipe, reached the client too.
    assert "late_item" in report["after_growth"], report
    # One per change, not one per upstream event.
    assert report["heard"] == 2, report
