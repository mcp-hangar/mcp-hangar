"""A failed start returns promptly when the upstream stays alive and silent.

The upstream (tests/silent_failing_provider.py) answers ``initialize``, fails
``tools/list``, and then stays up without writing to stderr. The start used to
read that process's stderr to the end to build its diagnostics. A live pipe has
no end, so the starting thread blocked there for as long as the process lived,
the server stayed ``initializing``, and every later caller waited 30 s for it
and timed out.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import signal
import sys
import threading
import time

import pytest

from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.value_objects import McpServerState

SILENT_PROVIDER = str(Path(__file__).resolve().parent.parent / "silent_failing_provider.py")

# Well inside the 30 s a waiter gives the starter, and far above what a failed
# start costs when nothing waits on the pipe: launch, two round trips, and the
# upstream's termination.
BOUND_S = 10.0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _read_pids(pid_dir: Path) -> list[int]:
    return [int(p.read_text()) for p in pid_dir.glob("*.pid") if p.read_text()]


class _Caller:
    """One ``ensure_ready`` call on its own thread, remembering how it ended."""

    def __init__(self, server: McpServer) -> None:
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(server,), daemon=True)

    def _run(self, server: McpServer) -> None:
        try:
            server.ensure_ready()
        except BaseException as exc:  # noqa: BLE001 -- the test inspects whatever ended the call
            self.error = exc

    def start(self) -> _Caller:
        self.thread.start()
        return self


@pytest.fixture
def pid_dir(tmp_path: Path):
    """Where each launched upstream records its pid; any survivor is killed after the test."""
    directory = tmp_path / "pids"
    directory.mkdir()
    yield directory
    for pid in _read_pids(directory):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


@pytest.fixture
def server(pid_dir: Path, monkeypatch: pytest.MonkeyPatch):
    # Each launch writes its own pid file: the helper overwrites the name it is
    # given, so a counter in the name keeps the second start's pid too.
    counter = iter(range(100))
    real_launch_config = McpServer._get_launch_config

    def launch_config(self: McpServer) -> dict:
        config = real_launch_config(self)
        env = dict(config.get("env") or {})
        env["SILENT_PROVIDER_PID_FILE"] = str(pid_dir / f"{next(counter)}.pid")
        return {**config, "env": env}

    monkeypatch.setattr(McpServer, "_get_launch_config", launch_config)
    mcp_server = McpServer(
        mcp_server_id="silent-failing",
        mode="subprocess",
        command=[sys.executable, SILENT_PROVIDER],
        max_consecutive_failures=5,
    )
    yield mcp_server
    with contextlib.suppress(Exception):
        mcp_server.shutdown()


def _wait_for_state(server: McpServer, state: McpServerState, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.state_snapshot == state:
            return True
        time.sleep(0.005)
    return False


def test_the_start_fails_promptly_and_releases_its_thread(server: McpServer) -> None:
    started = time.monotonic()
    first = _Caller(server).start()
    first.thread.join(timeout=BOUND_S)

    assert not first.thread.is_alive(), f"the starting thread is still blocked after {BOUND_S}s"
    assert isinstance(first.error, McpServerStartError), repr(first.error)
    assert "Failed to list tools" in str(first.error)
    assert time.monotonic() - started < BOUND_S
    assert server.state_snapshot in (McpServerState.DEAD, McpServerState.DEGRADED)


def test_a_second_caller_is_not_held_behind_the_first(server: McpServer) -> None:
    first = _Caller(server).start()
    # Join the first start while it is still in flight when we can, so the
    # second caller waits on it rather than starting its own. Either way it
    # must come back with the start error, not the 30 s waiter timeout.
    _wait_for_state(server, McpServerState.INITIALIZING, timeout=2.0)
    second = _Caller(server).start()

    first.thread.join(timeout=BOUND_S)
    second.thread.join(timeout=BOUND_S)

    assert not first.thread.is_alive(), f"the first caller is still blocked after {BOUND_S}s"
    assert not second.thread.is_alive(), f"the second caller is still blocked after {BOUND_S}s"
    assert isinstance(first.error, McpServerStartError), repr(first.error)
    assert isinstance(second.error, McpServerStartError), repr(second.error)
    assert server.state_snapshot in (McpServerState.DEAD, McpServerState.DEGRADED)


def test_a_failed_start_terminates_the_upstream(server: McpServer, pid_dir: Path) -> None:
    caller = _Caller(server).start()
    caller.thread.join(timeout=BOUND_S)
    assert not caller.thread.is_alive(), f"the starting thread is still blocked after {BOUND_S}s"

    pids = _read_pids(pid_dir)
    assert pids, "the upstream never started"
    # The start's failure path reaps the process it launched, so it is gone at
    # once rather than left running with no owner.
    assert not [pid for pid in pids if _pid_alive(pid)], "a failed start left its upstream running"
