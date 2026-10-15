"""`Hangar.start_mcp_server` and `SyncHangar.start_mcp_server` start the server (#1685).

Both called `McpServer.start`, which does not exist, so every call raised
`AttributeError`. The unit test passed because it mocked the server and asserted
the mock's `start` was called. This one boots the real facade on the mock
provider in a fresh interpreter, starts the server through each facade, and reads
its state back.
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
out = {}
if mode == "sync":
    with SyncHangar.from_config(config) as h:
        out["before"] = h.get_mcp_server("math").state
        h.start_mcp_server("math")
        out["after"] = h.get_mcp_server("math").state
        h.stop_mcp_server("math")
        out["stopped"] = h.get_mcp_server("math").state
else:
    async def main():
        async with Hangar.from_config(config) as h:
            out["before"] = (await h.get_mcp_server("math")).state
            await h.start_mcp_server("math")
            out["after"] = (await h.get_mcp_server("math")).state
            await h.stop_mcp_server("math")
            out["stopped"] = (await h.get_mcp_server("math")).state
            try:
                await h.start_mcp_server("nope")
            except Exception as e:
                out["missing"] = type(e).__name__
    asyncio.run(main())
print("RESULT " + json.dumps(out))
"""


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_the_facade_starts_and_stops_a_server(tmp_path, mode):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"mcp_servers:\n  math:\n    mode: subprocess\n    command: [{sys.executable}, {MOCK}]\n    idle_ttl_s: 600\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(config), mode], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-3000:]
    out = json.loads(next(line for line in result.stdout.splitlines() if line.startswith("RESULT "))[7:])
    assert out["before"] == "cold"
    assert out["after"] == "ready"
    assert out["stopped"] == "cold"
    if mode == "async":
        assert out["missing"] == "McpServerNotFoundError"
