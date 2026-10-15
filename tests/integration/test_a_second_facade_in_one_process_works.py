"""A second `Hangar` in one process works after the first was stopped (#1687).

Bootstrap registers its handlers on process-wide buses and nothing took them
down, so the second `Hangar.from_config` failed with `Handler already registered
for StartMcpServerCommand`. Stopping a facade now releases the runtime and the
buses, and the next one builds its own.

Each run is a fresh interpreter doing the whole cycle twice on the real bootstrap:
start the facade, start a server, call a tool, stop. The sync wrapper takes the
same path and is run the same way.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

MOCK = Path(__file__).resolve().parents[1] / "mock_provider.py"

SCRIPT = r"""
import asyncio, json, sys
from mcp_hangar.facade import Hangar, SyncHangar

config, mode = sys.argv[1], sys.argv[2]
out = []
if mode == "sync":
    for _ in range(2):
        with SyncHangar.from_config(config) as h:
            h.start_mcp_server("math")
            out.append({"state": h.get_mcp_server("math").state, "answer": h.invoke("math", "add", {"a": 1, "b": 2})})
else:
    async def main():
        for _ in range(2):
            async with Hangar.from_config(config) as h:
                await h.start_mcp_server("math")
                out.append({"state": (await h.get_mcp_server("math")).state,
                            "answer": await h.invoke("math", "add", {"a": 1, "b": 2})})
    asyncio.run(main())
print("RESULT " + json.dumps(out, default=str))
"""


@pytest.mark.parametrize("mode", ["async", "sync"])
def test_create_stop_create_again(tmp_path, mode):
    config = tmp_path / "config.yaml"
    config.write_text(f"mcp_servers:\n  math:\n    mode: subprocess\n    command: [{sys.executable}, {MOCK}]\n")
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(config), mode], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-3000:]
    line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT "))
    runs = json.loads(line[7:])
    assert [r["state"] for r in runs] == ["ready", "ready"]
    assert runs[0]["answer"] == runs[1]["answer"]
