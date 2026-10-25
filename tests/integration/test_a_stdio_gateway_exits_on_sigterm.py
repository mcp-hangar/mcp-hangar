"""A stdio gateway exits on SIGTERM while its client keeps stdin open (#2090).

The SDK reads stdin on a worker thread, which a read blocks while the pipe is
open, and the interpreter joins that thread before it exits: the gateway shut
everything down on SIGTERM and then kept running until stdin closed or a
SIGKILL. The real `serve` runs here in its own process, with a client that holds
the pipe open the way an MCP client does.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}


def _start(tmp_path: Path) -> subprocess.Popen[bytes]:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\nevent_store:\n  driver: memory\n")
    env = dict(os.environ, MCP_TRACING_ENABLED="false")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from mcp_hangar.server.cli import cli_main; cli_main()",
            "--config",
            str(config),
            "serve",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=(tmp_path / "stderr.log").open("wb"),
        env=env,
        cwd=tmp_path,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((json.dumps(_INITIALIZE) + "\n").encode())
    proc.stdin.flush()
    assert b'"id":1' in proc.stdout.readline()
    return proc


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_the_gateway_exits_with_stdin_still_open(tmp_path: Path, signum: int) -> None:
    proc = _start(tmp_path)
    try:
        started = time.monotonic()
        proc.send_signal(signum)
        rc = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert rc == 0
    assert time.monotonic() - started < 15
    log = (tmp_path / "stderr.log").read_text(errors="replace")
    assert "server_lifecycle_shutdown_complete" in log
    assert "stdio_exit_forced" in log


def test_closing_stdin_still_exits_on_its_own(tmp_path: Path) -> None:
    proc = _start(tmp_path)
    try:
        assert proc.stdin is not None
        proc.stdin.close()
        rc = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert rc == 0
    assert "stdio_exit_forced" not in (tmp_path / "stderr.log").read_text(errors="replace")
