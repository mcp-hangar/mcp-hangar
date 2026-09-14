"""An expected stdio process exit (idle shutdown) must not log at ERROR.

Regression: `_capture_process_stderr` logged `stdio_client_process_exited` at
ERROR unconditionally, so every idle-TTL shutdown (which sets self.closed first)
produced a spurious error, inflating the error stream / log-based alerting.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch


def _exited_stderr(content: bytes):
    """The stderr pipe of a process that has exited: its content, then EOF."""
    read_end, write_end = os.pipe()
    os.write(write_end, content)
    os.close(write_end)
    return os.fdopen(read_end, "r")


def _client(*, closed: bool, exit_code: int, stderr: bytes = b""):
    from mcp_hangar.stdio_client import StdioClient

    popen = MagicMock(spec=subprocess.Popen)
    popen.pid = 999
    popen.stdin = MagicMock()
    popen.stdout = MagicMock()
    popen.stderr = _exited_stderr(stderr)
    popen.poll.return_value = exit_code
    with patch("mcp_hangar.stdio_client.threading.Thread"):
        c = StdioClient(popen)
    c.closed = closed
    return c


def test_expected_exit_logs_info_not_error():
    client = _client(closed=True, exit_code=0)
    with patch("mcp_hangar.stdio_client.logger") as log:
        client._capture_process_stderr()

    info_events = [c.args[0] for c in log.info.call_args_list]
    error_events = [c.args[0] for c in log.error.call_args_list]
    assert "stdio_client_process_exited" in info_events
    assert "stdio_client_process_exited" not in error_events


def test_expected_exit_stderr_logs_info():
    client = _client(closed=True, exit_code=-15, stderr=b"terminated")
    with patch("mcp_hangar.stdio_client.logger") as log:
        client._capture_process_stderr()

    info_events = [c.args[0] for c in log.info.call_args_list]
    error_events = [c.args[0] for c in log.error.call_args_list]
    assert "stdio_client_process_stderr" in info_events
    assert "stdio_client_process_stderr" not in error_events


def test_unexpected_exit_still_logs_error():
    client = _client(closed=False, exit_code=1, stderr=b"boom")
    with patch("mcp_hangar.stdio_client.logger") as log:
        client._capture_process_stderr()

    error_events = [c.args[0] for c in log.error.call_args_list]
    assert "stdio_client_process_exited" in error_events
    assert "stdio_client_process_stderr" in error_events
