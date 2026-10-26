"""A stdio gateway exits on a shutdown signal that a thread other than the main one takes (#2103).

On Linux a process-directed SIGTERM may be taken by any thread that does not
block it. CPython's C handler then only sets a flag for the main thread, which
a stdio gateway keeps asleep in the event loop's `select` with no timeout: the
Python handler never ran and the gateway ignored the signal. Which thread takes
a `kill` is up to the kernel, so this sends the signal to one particular
non-main thread: the real `serve` runs in its own process with one extra thread,
which signals itself when the test asks.
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

# The gateway, plus a thread that sends `signum` to itself once `fire` exists.
_GATEWAY = """
import os, signal, sys, threading, time

fire, signum = sys.argv[1], int(sys.argv[2])
del sys.argv[1:3]

def signal_from_this_thread():
    while not os.path.exists(fire):
        time.sleep(0.05)
    signal.pthread_kill(threading.get_ident(), signum)

threading.Thread(target=signal_from_this_thread, name="signaller", daemon=True).start()
from mcp_hangar.server.cli import cli_main
cli_main()
"""


def _start(tmp_path: Path, signum: int) -> subprocess.Popen[bytes]:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\nevent_store:\n  driver: memory\n")
    env = dict(os.environ, MCP_TRACING_ENABLED="false", PYTHONFAULTHANDLER="1")
    proc = subprocess.Popen(
        [sys.executable, "-c", _GATEWAY, str(tmp_path / "fire"), str(int(signum)), "--config", str(config), "serve"],
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


def _wait(proc: subprocess.Popen[bytes], tmp_path: Path) -> int:
    """The gateway's exit code; a gateway still running after 30 s fails with its log and thread stacks."""
    try:
        return proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGABRT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log = (tmp_path / "stderr.log").read_text(errors="replace")
        pytest.fail(f"the gateway was still running 30 s later; its stderr ends with:\n{log[-12000:]}")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_the_gateway_exits_when_another_thread_takes_the_signal(tmp_path: Path, signum: int) -> None:
    proc = _start(tmp_path, signum)
    try:
        started = time.monotonic()
        (tmp_path / "fire").touch()
        rc = _wait(proc, tmp_path)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert rc == 0
    assert time.monotonic() - started < 15
    log = (tmp_path / "stderr.log").read_text(errors="replace")
    # The main thread slept through it until the wakeup watcher forwarded it there.
    assert "shutdown_signal_forwarded" in log
    assert "shutdown_signal_received" in log
    assert "server_lifecycle_shutdown_complete" in log
    assert "stdio_exit_forced" in log
