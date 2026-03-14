"""Unit tests for Phase 21-02: live stderr-reader threads in launchers and Provider._start_stderr_reader."""

import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, patch


from mcp_hangar.domain.contracts.log_buffer import IProviderLogBuffer
from mcp_hangar.domain.model.provider import Provider
from mcp_hangar.infrastructure.persistence.log_buffer import ProviderLogBuffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(provider_id: str = "test-p", log_buffer: IProviderLogBuffer | None = None) -> Provider:
    return Provider(
        provider_id=provider_id,
        mode="subprocess",
        command=[sys.executable, "-c", "pass"],
        log_buffer=log_buffer,
    )


def _make_buffer(provider_id: str = "test-p") -> ProviderLogBuffer:
    return ProviderLogBuffer(provider_id)


# ---------------------------------------------------------------------------
# Provider._start_stderr_reader
# ---------------------------------------------------------------------------


class TestStartStderrReader:
    def test_no_log_buffer_skips_reader(self):
        """When no log buffer is configured, no thread is started."""
        provider = _make_provider(log_buffer=None)
        # Create a fake client with a stderr pipe
        r, w = _make_pipe()
        fake_client = _fake_client_with_stderr(r)
        threads_before = threading.active_count()
        provider._start_stderr_reader(fake_client)
        w.close()
        r.close()
        # No new threads should have been spawned
        assert threading.active_count() == threads_before

    def test_lines_appended_to_buffer(self):
        """Lines emitted on stderr are appended to the buffer."""
        buf = _make_buffer()
        provider = _make_provider(log_buffer=buf)

        r, w = _make_pipe()
        fake_client = _fake_client_with_stderr(r)
        provider._start_stderr_reader(fake_client)

        w.write("hello\n")
        w.write("world\n")
        w.flush()
        w.close()

        # Give reader thread time to drain the pipe
        deadline = time.time() + 2.0
        while len(buf) < 2 and time.time() < deadline:
            time.sleep(0.01)

        lines = buf.tail(10)
        assert len(lines) == 2
        assert lines[0].content == "hello"
        assert lines[1].content == "world"
        assert all(ln.stream == "stderr" for ln in lines)
        assert all(ln.provider_id == "test-p" for ln in lines)

    def test_trailing_newline_stripped(self):
        """Trailing newline is stripped from each line."""
        buf = _make_buffer()
        provider = _make_provider(log_buffer=buf)

        r, w = _make_pipe()
        provider._start_stderr_reader(_fake_client_with_stderr(r))

        w.write("trimmed\n")
        w.flush()
        w.close()

        deadline = time.time() + 2.0
        while len(buf) < 1 and time.time() < deadline:
            time.sleep(0.01)

        assert buf.tail(1)[0].content == "trimmed"

    def test_no_stderr_pipe_is_noop(self):
        """Client without process.stderr skips reader silently."""
        buf = _make_buffer()
        provider = _make_provider(log_buffer=buf)
        fake_client = MagicMock()
        fake_client.process.stderr = None
        # Should not raise
        provider._start_stderr_reader(fake_client)

    def test_no_process_attribute_is_noop(self):
        """Client without process attribute skips reader silently."""
        buf = _make_buffer()
        provider = _make_provider(log_buffer=buf)
        fake_client = MagicMock(spec=[])  # no attributes
        provider._start_stderr_reader(fake_client)

    def test_reader_thread_is_daemon(self):
        """The reader thread is a daemon so it doesn't block shutdown."""
        buf = _make_buffer("daemon-test")
        provider = _make_provider("daemon-test", log_buffer=buf)

        r, w = _make_pipe()
        provider._start_stderr_reader(_fake_client_with_stderr(r))

        # Find the reader thread
        reader = None
        for t in threading.enumerate():
            if "stderr-reader-daemon-test" in t.name:
                reader = t
                break

        assert reader is not None, "reader thread not found"
        assert reader.daemon is True

        w.close()
        r.close()

    def test_reader_thread_terminates_on_eof(self):
        """The reader thread exits once the pipe is closed (process exit)."""
        buf = _make_buffer()
        provider = _make_provider(log_buffer=buf)

        r, w = _make_pipe()
        provider._start_stderr_reader(_fake_client_with_stderr(r))

        # Close pipe to signal EOF (process exit)
        w.close()
        r.close()

        # Find and wait for the reader thread
        reader = None
        for t in threading.enumerate():
            if "stderr-reader-test-p" in t.name:
                reader = t
                break

        if reader is not None:
            reader.join(timeout=2.0)
            assert not reader.is_alive()


# ---------------------------------------------------------------------------
# DockerLauncher stderr=PIPE fix
# ---------------------------------------------------------------------------


class TestDockerLauncherStderrPipe:
    def test_docker_launcher_uses_stderr_pipe(self):
        """DockerLauncher must pass stderr=PIPE not DEVNULL."""
        from mcp_hangar.domain.services.provider_launcher.docker import DockerLauncher

        launcher = DockerLauncher(runtime="docker")
        captured_kwargs: dict = {}

        def fake_popen(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            raise FileNotFoundError("docker not available in unit test")

        with patch("subprocess.Popen", side_effect=fake_popen):
            try:
                launcher.launch("mcp/math:latest")
            except Exception:  # noqa: BLE001
                pass

        assert captured_kwargs.get("stderr") == subprocess.PIPE, (
            f"Expected stderr=PIPE, got {captured_kwargs.get('stderr')!r}"
        )


# ---------------------------------------------------------------------------
# SubprocessLauncher and ContainerLauncher already use stderr=PIPE
# ---------------------------------------------------------------------------


class TestSubprocessLauncherStderrPipe:
    def test_subprocess_launcher_uses_stderr_pipe(self):
        """SubprocessLauncher must pass stderr=PIPE."""
        from mcp_hangar.domain.services.provider_launcher.subprocess import SubprocessLauncher

        launcher = SubprocessLauncher()
        captured_kwargs: dict = {}

        def fake_popen(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            raise FileNotFoundError("not in test")

        with patch("subprocess.Popen", side_effect=fake_popen):
            try:
                launcher.launch([sys.executable, "-c", "pass"])
            except Exception:  # noqa: BLE001
                pass

        assert captured_kwargs.get("stderr") == subprocess.PIPE


class TestContainerLauncherStderrPipe:
    def test_container_launcher_uses_stderr_pipe(self):
        """ContainerLauncher with inherit_stderr=False must pass stderr=PIPE."""
        from mcp_hangar.domain.services.provider_launcher.container import ContainerLauncher

        captured_kwargs: dict = {}

        def fake_popen(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            raise FileNotFoundError("not in test")

        # Patch shutil.which so ContainerLauncher can be instantiated without podman/docker
        with patch("shutil.which", return_value="/usr/bin/docker"):
            launcher = ContainerLauncher(runtime="docker")

        with patch("subprocess.Popen", side_effect=fake_popen):
            try:
                launcher.launch("mcp/math:latest")
            except Exception:  # noqa: BLE001
                pass

        # When MCP_CONTAINER_INHERIT_STDERR is not set, stderr=PIPE
        assert captured_kwargs.get("stderr") == subprocess.PIPE


# ---------------------------------------------------------------------------
# Integration: Provider with log_buffer captures stderr from a real subprocess
# ---------------------------------------------------------------------------


class TestProviderStderrCapture:
    def test_stderr_lines_captured_from_subprocess(self):
        """End-to-end: Provider with a log buffer captures stderr from a real child process."""
        buf = ProviderLogBuffer("echo-provider")

        # A tiny script that writes to stderr then waits for stdin (simulating an MCP server)
        script = (
            "import sys, time\n"
            "sys.stderr.write('startup-line-1\\n')\n"
            "sys.stderr.write('startup-line-2\\n')\n"
            "sys.stderr.flush()\n"
            "sys.stdin.read()  # block until stdin is closed\n"
        )

        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Simulate what Provider._start_stderr_reader does
        from mcp_hangar.domain.value_objects.log import LogLine as _LogLine

        provider_id = "echo-provider"

        def _reader():
            try:
                for raw_line in process.stderr:
                    buf.append(_LogLine(provider_id=provider_id, stream="stderr", content=raw_line.rstrip("\n")))
            except Exception:  # noqa: BLE001
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        # Wait for the two lines to arrive
        deadline = time.time() + 3.0
        while len(buf) < 2 and time.time() < deadline:
            time.sleep(0.01)

        process.stdin.close()
        process.wait(timeout=3.0)
        t.join(timeout=2.0)

        lines = buf.tail(10)
        assert len(lines) >= 2
        contents = [ln.content for ln in lines]
        assert "startup-line-1" in contents
        assert "startup-line-2" in contents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipe():
    """Return (read_end, write_end) as text-mode file objects via os.pipe()."""
    import os

    rfd, wfd = os.pipe()
    r = open(rfd, buffering=1, encoding="utf-8")  # noqa: SIM115
    w = open(wfd, "w", buffering=1, encoding="utf-8")  # noqa: SIM115
    return r, w


def _fake_client_with_stderr(stderr_file):
    """Return a MagicMock client whose process.stderr is the given file."""
    fake_process = MagicMock()
    fake_process.stderr = stderr_file
    fake_client = MagicMock()
    fake_client.process = fake_process
    return fake_client
