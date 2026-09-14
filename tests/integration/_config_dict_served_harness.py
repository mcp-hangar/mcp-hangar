"""Bootstrap Hangar from a file or a dict, and call a tool through the served app (#1415).

Run as a script, in its own interpreter, by
``test_a_config_dict_validator_refuses_a_served_call.py``:
``python _config_dict_served_harness.py <file|dict> <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime singleton, the executor's validator pipeline, the tool-access resolver
-- and two boots in one interpreter would read each other's.

What runs is production: ``bootstrap()`` with ``config_path`` or with
``config_dict``, then ``mcp_app_for_serving`` -- the app ``serve --http``
serves -- under starlette's ``TestClient``, taking stateless ``tools/call
hangar_call`` POSTs to ``tests/mock_provider.py`` over stdio. Nothing is stubbed
and no context is mocked. The configuration sets one ``payload_size`` validator,
which only the file path applied before #1415.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

import yaml

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "config-dict-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "hangar_call",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

#: The validator's cap. `add(1, 2)` is far under it; the padded call is far over.
MAX_BYTES = 256

CALLS: dict[str, dict[str, Any]] = {
    "small": {"a": 1, "b": 2},
    "oversized": {"a": 1, "b": 2, "pad": "x" * 2048},
}


def _config() -> dict[str, Any]:
    return {
        "mcp_servers": {"math": {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}},
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": MAX_BYTES}]},
        "config_reload": {"enabled": False},
    }


def _hangar_call(client: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": "math", "tool": "add", "arguments": arguments}]},
        "_meta": dict(ENVELOPE),
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=HEADERS, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    return json.loads(result["content"][0]["text"])


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    config = _config()
    if mode == "file":
        path = out.parent / "hangar.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        context = bootstrap(config_path=str(path))
    else:
        context = bootstrap(config_dict=config)

    app = mcp_app_for_serving(context.mcp_server)
    with TestClient(app, base_url=BASE_URL) as client:
        batches = {name: _hangar_call(client, arguments) for name, arguments in CALLS.items()}

    for server in context.runtime.repository.get_all().values():
        server.shutdown()

    out.write_text(json.dumps({"batches": batches}))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
