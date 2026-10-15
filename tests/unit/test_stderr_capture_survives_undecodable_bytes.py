"""Log capture continues past bytes the pipe's codec rejects (#1688).

The launchers opened the child's pipes with `text=True` and no encoding, so they
used the locale's codec and a strict error handler: one byte it could not decode
raised `UnicodeDecodeError` in the reader thread, which swallowed it, and capture
ended with no line saying so. MCP stdio is UTF-8; the launchers now say so and
replace what is not.
"""

from __future__ import annotations

import sys
import time
from unittest.mock import patch

import pytest

from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.infrastructure.persistence.log_buffer import ProviderLogBuffer

SCRIPT = (
    "import sys\n"
    "sys.stderr.buffer.write(b'before\\n')\n"
    "sys.stderr.buffer.write(b'bad \\xff\\xfe bytes\\n')\n"
    "sys.stderr.buffer.write(b'after\\n')\n"
    "sys.stderr.flush()\n"
)


def test_capture_continues_after_an_undecodable_line(tmp_path):
    from mcp_hangar.infrastructure.launchers.subprocess import SubprocessLauncher

    script = tmp_path / "noisy.py"
    script.write_text(SCRIPT)
    client = SubprocessLauncher().launch([sys.executable, str(script)])
    buffer = ProviderLogBuffer("noisy")
    server = McpServer(mcp_server_id="noisy", mode="subprocess", command=["x"], log_buffer=buffer)
    server._start_stderr_reader(client)
    client.process.wait(timeout=10)

    deadline = time.time() + 5
    while len(buffer) < 3 and time.time() < deadline:
        time.sleep(0.02)
    contents = [line.content for line in buffer.tail(10)]
    assert contents[0] == "before"
    assert "bad" in contents[1] and "�" in contents[1]
    assert contents[2] == "after"


@pytest.mark.parametrize("launcher", ["subprocess", "docker", "container"])
def test_every_launcher_decodes_as_utf8_and_replaces(launcher):
    seen: dict = {}

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs)
        raise FileNotFoundError("not in a unit test")

    with patch("subprocess.Popen", side_effect=fake_popen), patch("shutil.which", return_value="/usr/bin/docker"):
        try:
            if launcher == "subprocess":
                from mcp_hangar.infrastructure.launchers.subprocess import SubprocessLauncher

                SubprocessLauncher().launch([sys.executable, "-c", "pass"])
            elif launcher == "docker":
                from mcp_hangar.infrastructure.launchers.docker import DockerLauncher

                DockerLauncher(runtime="docker").launch("mcp/math:latest")
            else:
                from mcp_hangar.infrastructure.launchers.container import ContainerLauncher

                ContainerLauncher(runtime="docker").launch("mcp/math:latest")
        except Exception:  # noqa: BLE001 -- the fake raises; only the kwargs matter
            pass

    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"


def test_a_reader_that_stops_says_so(caplog):
    class _Broken:
        def __iter__(self):
            raise OSError("pipe closed under us")

    class _Process:
        stderr = _Broken()

    class _Client:
        process = _Process()

    server = McpServer(mcp_server_id="b", mode="subprocess", command=["x"], log_buffer=ProviderLogBuffer("b"))
    with patch("mcp_hangar.domain.model.mcp_server.logger") as log:
        server._start_stderr_reader(_Client())
        deadline = time.time() + 3
        while not log.warning.called and time.time() < deadline:
            time.sleep(0.02)
    assert log.warning.call_args.args[0] == "stderr_reader_stopped"
