"""Hangar as the MCP client under test, for the official conformance suite (#1902).

    npx @modelcontextprotocol/conformance client --command "python tests/conformance/hangar_client.py"

The suite starts a scenario's test server and appends its URL to the command.
Hangar meets upstreams as a client, so this puts that URL in a config as a
remote server, starts `serve --http` on it, and drives the gateway the way a
caller would: start the server (Hangar's initialize, or its stateless discover
on 2026-07-28), read its tools, and call the first one through `hangar_call`.
What the suite then judges is what Hangar sent the test server.

The endpoint is loopback, which the SSRF policy refuses for a server registered
over the API; a server declared in config.yaml is the operator's own and is not
checked, so the config file is the right door here.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SERVER = "upstream"
# The test server answers tools/list only: a tools/call would get -32601 and
# fail the run, as the reference client notes.
LIST_ONLY = {"json-schema-ref-no-deref"}
# SEP-1613: the schema Hangar shows for this tool is echoed back through that
# one, and the test server checks that `$schema`, `$defs` and the rest survived.
SCHEMA_SCENARIO = "json-schema-2020-12-preservation"
SCHEMA_TOOL, ECHO_TOOL = "json_schema_2020_12_tool", "json_schema_echo"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(method: str, url: str) -> dict:
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


async def _call(gateway: str, tool: str, arguments: dict) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(gateway + "/mcp") as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "hangar_call", {"calls": [{"mcp_server": SERVER, "tool": tool, "arguments": arguments}]}
            )
            print(result.content[0].text, file=sys.stderr)


def main() -> int:
    url = sys.argv[-1]
    scenario = os.environ.get("MCP_CONFORMANCE_SCENARIO", "")
    port = _free_port()
    gateway = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.yaml"
        config.write_text(
            f"logging:\n  level: WARNING\nmcp_servers:\n  {SERVER}:\n    mode: remote\n"
            f"    endpoint: {json.dumps(url)}\n    idle_ttl_s: 300\n",
            encoding="utf-8",
        )
        log = open(Path(tmp) / "gateway.log", "w+", encoding="utf-8")  # noqa: SIM115 -- read back on failure
        gateway_process = subprocess.Popen(
            [
                str(Path(sys.executable).with_name("mcp-hangar")),
                "--config",
                str(config),
                "serve",
                "--http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(120):
                try:
                    _request("GET", gateway + "/health/live")
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("the gateway never became healthy")
            _request("POST", f"{gateway}/api/mcp_servers/{SERVER}/start")
            tools = _request("GET", f"{gateway}/api/mcp_servers/{SERVER}/tools").get("tools", [])
            print(f"listed {[t.get('name') for t in tools]}", file=sys.stderr)
            if scenario == SCHEMA_SCENARIO:
                shown = next((t for t in tools if t.get("name") == SCHEMA_TOOL), {})
                asyncio.run(_call(gateway, ECHO_TOOL, {"schema": shown.get("inputSchema", {})}))
            elif tools and scenario not in LIST_ONLY:
                asyncio.run(_call(gateway, tools[0]["name"], {"a": 2, "b": 3}))
            return 0
        except Exception:
            log.seek(0)
            print(log.read()[-4000:], file=sys.stderr)
            raise
        finally:
            gateway_process.terminate()
            gateway_process.wait(timeout=30)


if __name__ == "__main__":
    sys.exit(main())
