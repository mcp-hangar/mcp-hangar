"""Serve a server whose upstream serves an undeclared tool, and ``hangar_call`` it.

Run as a script, in its own interpreter, by
``test_a_capability_block_stops_the_call_that_found_it.py``:
``python _capability_block_harness.py <enforcement_mode> <out.json>``. Not
collected by pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the command bus, the saga manager -- that a second bootstrap in the same
interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict, the app
``serve --http`` serves under starlette's ``TestClient``, and ``hangar_call`` to
``tests/undeclared_tool_provider.py`` over stdio. The server declares ``add`` in
``expected_tools``, and the upstream also serves ``exfiltrate``.

The harness asks the OS which of the upstream's processes still run before any
server is shut down. A process the gateway left open is exactly what is under
test, and a shutdown would close it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

PROVIDER = Path(__file__).resolve().parents[1] / "undeclared_tool_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "capability-block-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

SERVER = "drifting"
DECLARED, UNDECLARED = "add", "exfiltrate"
#: Calls to the undeclared tool, one after another.
REPEATS = 3


def _config(mode: str, record: Path) -> dict[str, Any]:
    return {
        "mcp_servers": {
            SERVER: {
                "mode": "subprocess",
                "command": [sys.executable, str(PROVIDER)],
                "env": {"UNDECLARED_PROVIDER_RECORD": str(record)},
                "capabilities": {"tools": {"expected_tools": [DECLARED]}, "enforcement_mode": mode},
            }
        }
    }


def _tool(client: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One stateless ``tools/call`` POST to ``/mcp``; the tool's JSON result."""
    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    params = {"name": name, "arguments": arguments, "_meta": ENVELOPE}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    return json.loads(result["content"][0]["text"])


def _call(client: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """``hangar_call`` one tool on the server; the one result in the batch."""
    batch = _tool(client, "hangar_call", {"calls": [{"mcp_server": SERVER, "tool": tool, "arguments": arguments}]})
    [result] = batch["results"]
    return dict(result)


def _running(pid: int) -> bool:
    """Whether ``pid`` is a live process: ``ps`` lists it, and not as a zombie."""
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import (
        CapabilityViolationDetected,
        DomainEvent,
        McpServerCapabilityQuarantined,
        McpServerStarted,
    )
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    record = out.parent / "upstream.jsonl"
    context = bootstrap(config_dict=_config(mode, record))

    seen: list[str] = []

    def observe(event: DomainEvent) -> None:
        if isinstance(event, (CapabilityViolationDetected, McpServerCapabilityQuarantined, McpServerStarted)):
            seen.append(type(event).__name__)

    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    report: dict[str, Any] = {}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        report["undeclared"] = [_call(client, UNDECLARED, {"text": "hi"}) for _ in range(REPEATS)]
        report["declared"] = _call(client, DECLARED, {"a": 1, "b": 2})

    entries = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
    launched = [entry["pid"] for entry in entries if entry["event"] == "start"]
    report["state"] = context.runtime.repository.get(SERVER).state.value
    report["launched"] = launched
    report["running"] = [pid for pid in launched if _running(pid)]
    report["tools_called"] = [entry["tool"] for entry in entries if entry.get("method") == "tools/call"]
    report["events"] = seen

    # Taken above, before these shutdowns, which are not under test.
    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
