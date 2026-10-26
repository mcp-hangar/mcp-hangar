"""A shutdown signal runs the shutdown once, whether the handler or the wakeup watcher gets to it (#2103).

Over stdio a wakeup fd carries a SIGTERM/SIGINT that a thread other than the
main one took to a watcher thread, because the main thread may never wake to run
the Python handler. When it does wake, both see the same signal: the handler
keeps today's path, the watcher leaves it a head start and stands down, and the
one that started first owns the shutdown. A watcher that sees no handler sends
the signal to the main thread once before it shuts down itself.
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


@pytest.fixture(autouse=True)
def pthread_kill():
    # A real forward would signal the test runner's own main thread.
    with patch("mcp_hangar.server.lifecycle.signal.pthread_kill") as pthread_kill:
        yield pthread_kill


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


def test_the_watcher_hands_the_signal_to_the_main_thread_first(end_after: MagicMock, pthread_kill: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    def the_main_thread_runs_the_handler(_ident: int, signum: int) -> None:
        with pytest.raises(SystemExit):
            signals.handler(signum, None)

    pthread_kill.side_effect = the_main_thread_runs_the_handler
    signals.on_wakeup(signal.SIGTERM)
    # The forwarded signal's own byte.
    signals.on_wakeup(signal.SIGTERM)

    pthread_kill.assert_called_once_with(threading.main_thread().ident, signal.SIGTERM)
    lifecycle.shutdown.assert_called_once()
    end_after.assert_called_once()


def test_the_watcher_shuts_down_when_the_handler_never_runs(end_after: MagicMock, pthread_kill: MagicMock) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    signals.on_wakeup(signal.SIGTERM)
    signals.on_wakeup(signal.SIGTERM)

    pthread_kill.assert_called_once()
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


def test_other_signals_on_the_wakeup_fd_are_left_to_their_own_handlers(
    end_after: MagicMock, pthread_kill: MagicMock
) -> None:
    lifecycle = MagicMock()
    signals = _signals(lifecycle)

    signals.on_wakeup(signal.SIGHUP)
    signals.on_wakeup(signal.SIGCHLD)

    pthread_kill.assert_not_called()
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


def _main_thread_only() -> None:
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("set_wakeup_fd needs the main thread")


def test_stopping_the_watcher_unregisters_the_fd_and_ends_its_thread(end_after: MagicMock) -> None:
    _main_thread_only()
    lifecycle = MagicMock()
    signals = _signals(lifecycle)
    assert signals.watch() is True
    thread = signals._thread
    assert thread is not None and thread.is_alive()

    signals.stop_watching()
    signals.stop_watching()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert signal.set_wakeup_fd(-1) == -1
    # A byte still in flight is not acted on.
    signals.on_wakeup(signal.SIGTERM)
    lifecycle.shutdown.assert_not_called()


def test_a_second_stdio_setup_stops_the_first_watcher() -> None:
    _main_thread_only()
    from mcp_hangar.server import lifecycle as module

    handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    reloaders = []
    try:
        reloaders.append(module._setup_signal_handlers(MagicMock(), stdio=True))
        first = module._signal_watcher
        assert first is not None and first._thread is not None
        reloaders.append(module._setup_signal_handlers(MagicMock(), stdio=True))
        first._thread.join(timeout=5)

        assert not first._thread.is_alive()
        assert module._signal_watcher is not None and module._signal_watcher is not first
    finally:
        module._stop_signal_watcher()
        for reloader in reloaders:
            if reloader is not None:
                reloader.stop(join_timeout_s=5.0)
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    assert signal.set_wakeup_fd(-1) == -1
