"""The flat projection must be importable before the server composition root.

A fresh interpreter, because that is the whole defect: in-process the suite
always has `mcp_hangar.server` in `sys.modules` already, which is exactly what
hid the cycle until someone ran a single test file on its own (#894).
"""

from __future__ import annotations

import subprocess
import sys


def test_flat_projection_imports_first_in_a_fresh_process() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import mcp_hangar.fastmcp_server.flat_tool_projection"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
