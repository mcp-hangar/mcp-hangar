"""The flat projection must be importable before the server composition root."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_flat_projection_imports_first_in_a_fresh_process() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source_root = repo_root / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(source_root), env.get("PYTHONPATH", "")) if path
    )

    result = subprocess.run(
        [sys.executable, "-c", "import mcp_hangar.fastmcp_server.flat_tool_projection"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
