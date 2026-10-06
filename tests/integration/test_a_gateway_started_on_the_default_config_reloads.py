"""A gateway started with no ``--config`` reloads the file it booted from (#1657).

``mcp-hangar serve`` with no flag and no ``MCP_CONFIG`` used to boot from the
implicit ``./config.yaml`` and hand bootstrap no path. Every reload then had
nothing to read: ``POST /api/config/reload`` and SIGHUP failed with "No
configuration path specified", and the watcher never started. The file `init`
writes, ``~/.config/mcp-hangar/config.yaml``, was not read at all.

So this runs the shipped console script in a subprocess, with ``HOME`` pointed
at a scratch directory and the file in each of the two default places: where
`init` writes it (the working directory has no ``config.yaml``), and
``./config.yaml``, which still wins when it exists. Each reload trigger is
asserted on what the process answers and logs: REST over a real socket, SIGHUP
as a real signal, and the watcher on a real file edit.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml

from tests._hangar_executable import hangar_executable

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGHUP is POSIX-only")

BOOT_TIMEOUT_S = 60
LOG_TIMEOUT_S = 30


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for(log: Path, needle: str, *, after: int = 0) -> str:
    """Wait for ``needle`` in the log past offset ``after``; return that part of the log."""
    deadline = time.monotonic() + LOG_TIMEOUT_S
    while time.monotonic() < deadline:
        text = log.read_text(encoding="utf-8", errors="replace")[after:]
        if needle in text:
            return text
        time.sleep(0.2)
    raise AssertionError(f"{needle!r} never logged; log:\n{log.read_text(encoding='utf-8', errors='replace')}")


@pytest.fixture(params=["init_path", "cwd"])
def gateway(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[str, subprocess.Popen[bytes], Path, Path]]:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    home.mkdir()
    if request.param == "cwd":
        config = work / "config.yaml"
    else:
        config = home / ".config" / "mcp-hangar" / "config.yaml"
        config.parent.mkdir(parents=True)
    config.write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {},
                # Polling, at the shortest interval, so the watcher's reload is quick to see.
                "config_reload": {"enabled": True, "use_watchdog": False, "interval_s": 1},
            }
        ),
        encoding="utf-8",
    )

    env = {k: v for k, v in os.environ.items() if k not in ("MCP_CONFIG", "MCP_MODE", "MCP_HTTP_PORT")}
    env["HOME"] = str(home)
    port = _free_port()
    log = tmp_path / "gateway.log"
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [hangar_executable(), "serve", "--http", "--host", "127.0.0.1", "--port", str(port)],
            cwd=work,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while True:
            assert process.poll() is None, log.read_text(encoding="utf-8", errors="replace")
            try:
                if httpx.get(f"{base_url}/health", timeout=1).status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, log.read_text(encoding="utf-8", errors="replace")
            time.sleep(0.2)
        yield base_url, process, config, log
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def test_rest_sighup_and_the_watcher_reload_the_default_file(gateway) -> None:
    base_url, process, config, log = gateway

    # The process read that file, not the built-in demo config.
    booted = log.read_text(encoding="utf-8", errors="replace")
    assert "loading_config_from_file" in booted, booted
    assert "config_not_found_using_default" not in booted

    response = httpx.post(f"{base_url}/api/config/reload", json={}, timeout=30)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "reloaded"

    mark = len(log.read_text(encoding="utf-8", errors="replace"))
    process.send_signal(signal.SIGHUP)
    after_signal = _wait_for(log, "config_reload_completed_via_signal", after=mark)
    assert "No configuration path specified" not in after_signal

    mark = len(log.read_text(encoding="utf-8", errors="replace"))
    # A different mtime and a different body: the poller compares the mtime.
    time.sleep(1.1)
    config.write_text(config.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    after_edit = _wait_for(log, "triggering_config_reload", after=mark)
    assert str(config) in after_edit
