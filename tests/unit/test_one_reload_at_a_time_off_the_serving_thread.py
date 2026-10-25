"""A reload runs one at a time, and SIGHUP never runs it on the serving thread.

SIGHUP ran the reload inside its signal handler, on the main thread, which is
the thread whose event loop serves HTTP. A graceful reload waits for the calls
in flight, so the gateway answered nothing, not even `/health/live`, until they
had finished. A second SIGHUP ran a second reload nested in the first.

And nothing serialised reloads: the file watcher, SIGHUP, the MCP tool and REST
could each run one at once. They interleaved: every reload computed its diff
before any stopped a server, and one server was stopped twice.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from unittest.mock import Mock, patch

import httpx
import pytest
import yaml
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands import reload_handler as reload_module
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.server.config import ServerConfigLoader
from mcp_hangar.server.lifecycle import SignalReloader, _setup_signal_handlers

pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP on this platform")

WAIT_S = 5.0


class _SlowReloads:
    """A command bus whose first reload runs until released; it counts every reload."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.first_started = threading.Event()
        self.second_done = threading.Event()
        self.sent: list[ReloadConfigurationCommand] = []

    def send(self, command: ReloadConfigurationCommand) -> dict:
        self.sent.append(command)
        if len(self.sent) == 1:
            self.first_started.set()
            self.release.wait(WAIT_S)
        elif len(self.sent) == 2:
            self.second_done.set()
        return {"success": True}


@pytest.fixture
def sighup_handler():
    """Install the server's signal handlers on a lifecycle stand-in; yield a factory for the SIGHUP one."""
    reloaders: list[SignalReloader] = []

    def install(bus: _SlowReloads):
        lifecycle = Mock()
        lifecycle._shutdown_requested = False
        lifecycle._context.runtime.command_bus = bus
        with patch("mcp_hangar.server.lifecycle.signal.signal") as register:
            reloader = _setup_signal_handlers(lifecycle)
        assert reloader is not None
        reloaders.append(reloader)
        handlers = {call.args[0]: call.args[1] for call in register.call_args_list}
        return handlers[signal.SIGHUP]

    yield install
    for reloader in reloaders:
        reloader.stop(join_timeout_s=WAIT_S)


def test_a_sighup_reload_does_not_stop_the_loop_serving_requests(sighup_handler):
    """The handler runs on the loop thread, as CPython runs it there; a request is still served."""
    bus = _SlowReloads()
    handler = sighup_handler(bus)

    async def live(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/health/live", live)])

    async def probe() -> int:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
            return (await client.get("/health/live")).status_code

    loop = asyncio.new_event_loop()
    serving = threading.Thread(target=loop.run_forever, daemon=True)
    serving.start()
    try:
        loop.call_soon_threadsafe(handler, signal.SIGHUP, None)
        assert bus.first_started.wait(WAIT_S), "SIGHUP never started a reload"
        # The reload is running, and waits until released: the loop must answer meanwhile.
        status = asyncio.run_coroutine_threadsafe(probe(), loop).result(timeout=2.0)
        assert status == 200
    finally:
        bus.release.set()
        loop.call_soon_threadsafe(loop.stop)
        serving.join(WAIT_S)
        loop.close()

    assert bus.sent[0].requested_by == "sighup"
    assert bus.sent[0].graceful is True


def test_sighups_during_a_reload_ask_for_exactly_one_more(sighup_handler):
    bus = _SlowReloads()
    handler = sighup_handler(bus)

    handler(signal.SIGHUP, None)
    assert bus.first_started.wait(WAIT_S)
    for _ in range(5):
        handler(signal.SIGHUP, None)
    bus.release.set()

    assert bus.second_done.wait(WAIT_S), "the SIGHUPs sent during the reload were dropped"
    assert len(bus.sent) == 2


def test_no_reload_starts_once_the_server_is_shutting_down():
    sent: list = []
    done = threading.Event()
    reloader = SignalReloader(sent.append, is_stopping=lambda: True)
    with patch("mcp_hangar.server.lifecycle.logger") as log:
        log.info.side_effect = lambda event, **_k: done.set() if event == "config_reload_skipped_via_signal" else None
        reloader.request()
        assert done.wait(WAIT_S)
    reloader.stop(join_timeout_s=WAIT_S)
    assert sent == []


class _Server:
    """A running server the reloaded file replaces; its stop runs until released."""

    def __init__(self, stopping: _Stops) -> None:
        self.mcp_server_id = "busy"
        self._stopping = stopping

    def shutdown(self, *_a, **_k) -> None:
        self._stopping.enter()


class _Stops:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.now = 0
        self.most = 0
        self.first_in = threading.Event()
        self.second_in = threading.Event()
        self.release = threading.Event()
        self.count = 0

    def enter(self) -> None:
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
            self.count += 1
            (self.first_in if self.count == 1 else self.second_in).set()
        if self.count == 1:
            self.release.wait(WAIT_S)
        with self.lock:
            self.now -= 1


class _WatchedLog:
    """Stands in for the reload handler's logger and says when a reload starts to wait."""

    def __init__(self) -> None:
        self.waiting = threading.Event()

    def info(self, event: str, **_k) -> None:
        if event == "config_reload_waiting":
            self.waiting.set()

    def warning(self, *_a, **_k) -> None:
        pass

    def error(self, *_a, **_k) -> None:
        pass


def test_two_reloads_never_run_at_once(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({"mcp_servers": {"busy": {"mode": "subprocess", "command": ["python", "-c", "pass"]}}}))
    log = _WatchedLog()
    monkeypatch.setattr(reload_module, "logger", log)
    stops = _Stops()

    def handler() -> ReloadConfigurationHandler:
        # Two handlers, as two triggers would each reach one: the lock is the process's.
        repo = Mock()
        repo.get_all.return_value = {"busy": _Server(stops)}
        return ReloadConfigurationHandler(repo, Mock(), config_loader=ServerConfigLoader())

    results: dict[str, dict] = {}

    def reload(name: str, by: ReloadConfigurationHandler) -> None:
        results[name] = by.handle(ReloadConfigurationCommand(config_path=str(path), requested_by=name))

    first = threading.Thread(target=reload, args=("file_watcher", handler()))
    second: threading.Thread | None = None
    first.start()
    try:
        assert stops.first_in.wait(WAIT_S), "the first reload never reached its stop"
        second = threading.Thread(target=reload, args=("sighup", handler()))
        second.start()
        # Either it waits, as it must, or it runs into the first one's stop.
        for _ in range(int(WAIT_S / 0.05)):
            if log.waiting.is_set() or stops.second_in.is_set():
                break
            log.waiting.wait(0.05)
        assert not stops.second_in.is_set(), "a second reload ran while the first was stopping a server"
        assert log.waiting.is_set()
    finally:
        stops.release.set()
        first.join(WAIT_S)
        if second is not None:
            second.join(WAIT_S)

    assert stops.most == 1
    assert stops.count == 2
    assert results["file_watcher"]["success"] and results["sighup"]["success"]
