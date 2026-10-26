"""A shutdown signal runs the shutdown once, whether the handler or the wakeup watcher gets to it (#2103).

Over stdio a wakeup fd carries a SIGTERM/SIGINT that a thread other than the
main one took to a watcher thread, because the main thread may never wake to run
the Python handler. When it does wake, both see the same signal: the handler
keeps today's path, the watcher leaves it a head start and stands down, and the
one that started first owns the shutdown.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.server.lifecycle import _ShutdownSignals

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")


@pytest.fixture
def end_after():
    with patch("mcp_hangar.server.lifecycle._end_after") as end_after:
        yield end_after


def _signals(lifecycle: MagicMock) -> _ShutdownSignals:
    return _ShutdownSignals(lifecycle, stdio=True, head_start_s=0.05)


def test_the_watcher_stands_down_when_the_handler_got_there(end_after: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    with pytest.raises(SystemExit):
        signals.handler(signal.SIGTERM, None)
    signals.on_wakeup(signal.SIGTERM)

    lifecycle.shutdown.assert_called_once()
    end_after.assert_called_once()


def test_the_watcher_shuts_down_when_the_handler_never_runs(end_after: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    signals.on_wakeup(signal.SIGTERM)

    lifecycle.shutdown.assert_called_once()
    end_after.assert_called_once()


def test_a_handler_that_wakes_after_the_watcher_took_over_stands_down(end_after: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)
    signals.on_wakeup(signal.SIGINT)

    # No SystemExit: the watcher owns the shutdown and the bounded exit.
    signals.handler(signal.SIGINT, None)

    lifecycle.shutdown.assert_called_once()
    end_after.assert_called_once()


def test_a_second_signal_into_the_handler_runs_it_again(end_after: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    for _ in range(2):
        with pytest.raises(SystemExit):
            signals.handler(signal.SIGINT, None)

    assert lifecycle.shutdown.call_count == 2


def test_a_failed_shutdown_on_the_watcher_still_ends_the_process(end_after: MagicMock) -> None:
    lifecycle = MagicMock()
    lifecycle.shutdown.side_effect = RuntimeError("stuck")
    signals = _signals(lifecycle)

    with pytest.raises(RuntimeError):
        signals.on_wakeup(signal.SIGTERM)

    end_after.assert_called_once()


def test_other_signals_on_the_wakeup_fd_are_left_to_their_own_handlers(end_after: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    signals.on_wakeup(signal.SIGHUP)
    signals.on_wakeup(signal.SIGCHLD)

    lifecycle.shutdown.assert_not_called()
    end_after.assert_not_called()


def test_no_watcher_off_the_main_thread() -> None:
    watching: list[bool] = []
    thread = threading.Thread(target=lambda: watching.append(_signals(MagicMock()).watch()))
    thread.start()
    thread.join()

    assert watching == [False]


def test_a_wakeup_fd_someone_else_registered_is_left_in_place() -> None:
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("set_wakeup_fd needs the main thread")

    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    previous = signal.set_wakeup_fd(write_fd)
    try:
        assert _signals(MagicMock()).watch() is False
        assert signal.set_wakeup_fd(write_fd) == write_fd
    finally:
        signal.set_wakeup_fd(previous)
        os.close(read_fd)
        os.close(write_fd)
