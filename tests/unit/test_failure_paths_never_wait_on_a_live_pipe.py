"""No failure path waits on the stderr of a process that is still running.

``read()`` on a pipe returns only at EOF, and a pipe a live process holds has
none. Each of these read one on a failure path:

- ``collect_startup_diagnostics`` and ``McpServer._log_client_error`` read the
  upstream's stderr when the client had captured none. They run on the starting
  thread, so a failed start blocked for as long as the upstream lived.
- ``StdioClient._capture_process_stderr`` read it on the stdout reader thread
  once stdout reached EOF. A process that closed stdout and kept running, or a
  descendant holding the pipe, kept that thread there, and the calls it had in
  flight were never failed.

Each pipe here is a real one whose write end stays open, so a read to EOF would
never return. Every call runs on its own thread and is given a bound, so a
regression fails the test rather than hanging the suite.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import os
from queue import Queue
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any, IO
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.services.error_diagnostics import collect_startup_diagnostics
from mcp_hangar.stdio_client import _STDERR_DRAIN_S, PendingRequest, StdioClient

BOUND_S = 5.0


@pytest.fixture
def live_pipe() -> Iterator[tuple[IO[str], int]]:
    """A pipe whose writer is still there: the read end never reaches EOF."""
    read_end, write_end = os.pipe()
    reader = os.fdopen(read_end, "r")
    yield reader, write_end
    with contextlib.suppress(OSError):
        os.close(write_end)
    reader.close()


def _within(fn: Callable[[], Any], seconds: float = BOUND_S) -> tuple[bool, Any]:
    """Run ``fn`` on a daemon thread; report whether it returned in time, and what."""
    box: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: box.update(value=fn()), daemon=True)
    worker.start()
    worker.join(timeout=seconds)
    return not worker.is_alive(), box.get("value")


def _live_client(stderr: IO[str], *, last_stderr: str | None = None) -> SimpleNamespace:
    """A client whose upstream is still running and captured nothing yet."""
    return SimpleNamespace(process=SimpleNamespace(poll=lambda: None, stderr=stderr), _last_stderr=last_stderr)


def test_diagnostics_do_not_read_a_live_process_pipe(live_pipe: tuple[IO[str], int]) -> None:
    stderr, write_end = live_pipe
    os.write(write_end, b"still running\n")

    returned, diagnostics = _within(lambda: collect_startup_diagnostics(_live_client(stderr)))

    assert returned, "collect_startup_diagnostics waited on a live process's stderr"
    assert diagnostics == {"stderr": None, "exit_code": None, "suggestion": None}


def test_diagnostics_use_what_the_client_captured(live_pipe: tuple[IO[str], int]) -> None:
    stderr, _ = live_pipe
    client = _live_client(stderr, last_stderr="ModuleNotFoundError: No module named 'x'")

    returned, diagnostics = _within(lambda: collect_startup_diagnostics(client))

    assert returned
    assert diagnostics["stderr"] == "ModuleNotFoundError: No module named 'x'"
    assert diagnostics["suggestion"].startswith("Install missing Python dependencies")


def test_logging_a_client_error_does_not_read_a_live_process_pipe(live_pipe: tuple[IO[str], int]) -> None:
    stderr, write_end = live_pipe
    os.write(write_end, b"still running\n")
    server = McpServer(mcp_server_id="live-pipe", mode="subprocess", command=["unused"])

    returned, _ = _within(lambda: server._log_client_error(_live_client(stderr)))

    assert returned, "_log_client_error waited on a live process's stderr"


def _unstarted_client(stdout: IO[str], stderr: IO[str]) -> StdioClient:
    popen = MagicMock(spec=subprocess.Popen)
    popen.pid = 4242
    popen.stdin = MagicMock()
    popen.stdout = stdout
    popen.stderr = stderr
    popen.poll.return_value = None
    with patch("mcp_hangar.stdio_client.threading.Thread"):
        return StdioClient(popen)


def test_stdout_eof_with_stderr_held_open_still_fails_the_calls_in_flight(
    live_pipe: tuple[IO[str], int],
) -> None:
    stderr, write_end = live_pipe
    os.write(write_end, b"boom\n")
    stdout_read, stdout_write = os.pipe()
    os.close(stdout_write)  # stdout is at EOF; stderr's writer is still there
    with os.fdopen(stdout_read, "r") as stdout:
        client = _unstarted_client(stdout, stderr)
        answer: Queue[dict[str, Any]] = Queue(maxsize=1)
        client.pending["req-1"] = PendingRequest(request_id="req-1", result_queue=answer, started_at=time.time())

        started = time.monotonic()
        returned, _ = _within(client._reader_loop)
        elapsed = time.monotonic() - started

    assert returned, "the stdout reader waited on stderr that never reaches EOF"
    assert elapsed < _STDERR_DRAIN_S + 2.0
    # What the process wrote before the deadline is kept, and the call in
    # flight is failed at once rather than left to its own timeout.
    assert client._last_stderr == "boom"
    assert answer.get_nowait() == {"error": {"code": -1, "message": "reader_died: boom"}}
