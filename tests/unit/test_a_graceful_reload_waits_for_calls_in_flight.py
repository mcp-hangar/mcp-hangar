"""A graceful reload lets the calls in flight finish before it stops the server (#1662).

Both reload modes called `shutdown()` at once, so `graceful` promised a drain and
did not drain. A call in flight to a server the reload replaces was cut off.

The handler is the real `ReloadConfigurationHandler` with the real
`ServerConfigLoader`, wired the way bootstrap wires it: its drain is
`ConcurrencyManager.wait_idle`. The "call in flight" is a real slot taken on a
real `ConcurrencyManager`, which is what the batch executor holds for the length
of a call.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands import reload_handler as reload_module
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.server.config import ServerConfigLoader
from mcp_hangar.server.tools.batch.concurrency import ConcurrencyManager

SERVER = "busy"


class _Running:
    """A running server whose spec no longer matches the file, so a reload replaces it."""

    def __init__(self, on_shutdown) -> None:
        self.mcp_server_id = SERVER
        self._on_shutdown = on_shutdown

    def shutdown(self, *_a, **_k) -> None:
        self._on_shutdown()


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({"mcp_servers": {SERVER: {"mode": "subprocess", "command": ["python", "-c", "pass"]}}}))
    return str(path)


def _handler(concurrency: ConcurrencyManager | None, on_shutdown) -> ReloadConfigurationHandler:
    repo = Mock()
    repo.get_all.return_value = {SERVER: _Running(on_shutdown)}
    drain = concurrency.wait_idle if concurrency is not None else None
    return ReloadConfigurationHandler(repo, Mock(), config_loader=ServerConfigLoader(), drain=drain)


def _call_in_flight(concurrency: ConcurrencyManager, hold_s: float) -> tuple[threading.Thread, float]:
    taken = threading.Event()
    released_at: list[float] = []

    def call() -> None:
        with concurrency.acquire(SERVER):
            taken.set()
            time.sleep(hold_s)
        released_at.append(time.monotonic())

    thread = threading.Thread(target=call)
    thread.start()
    assert taken.wait(5)
    return thread, released_at


def test_a_call_in_flight_completes_before_the_server_is_stopped(config_file):
    concurrency = ConcurrencyManager()
    stopped_at: list[float] = []
    handler = _handler(concurrency, lambda: stopped_at.append(time.monotonic()))
    thread, released_at = _call_in_flight(concurrency, hold_s=0.3)

    handler.handle(ReloadConfigurationCommand(config_path=config_file, graceful=True))
    thread.join()

    assert stopped_at, "the server was never stopped"
    assert stopped_at[0] >= released_at[0], "the server was stopped while a call was still in flight"


def test_a_forced_reload_does_not_wait(config_file):
    concurrency = ConcurrencyManager()
    stopped_at: list[float] = []
    handler = _handler(concurrency, lambda: stopped_at.append(time.monotonic()))
    thread, released_at = _call_in_flight(concurrency, hold_s=0.5)

    handler.handle(ReloadConfigurationCommand(config_path=config_file, graceful=False))
    thread.join()

    assert stopped_at[0] < released_at[0], "a forced reload waited for the call"


def test_the_wait_is_bounded(config_file, monkeypatch):
    monkeypatch.setattr(reload_module, "GRACEFUL_DRAIN_TIMEOUT_S", 0.1)
    concurrency = ConcurrencyManager()
    stopped_at: list[float] = []
    handler = _handler(concurrency, lambda: stopped_at.append(time.monotonic()))
    thread, released_at = _call_in_flight(concurrency, hold_s=0.6)

    started = time.monotonic()
    handler.handle(ReloadConfigurationCommand(config_path=config_file, graceful=True))
    thread.join()

    assert stopped_at[0] - started < 0.5, "the reload waited past its bound"
    assert stopped_at[0] < released_at[0]


def test_an_idle_server_is_stopped_without_waiting(config_file):
    stopped_at: list[float] = []
    handler = _handler(ConcurrencyManager(), lambda: stopped_at.append(time.monotonic()))

    started = time.monotonic()
    handler.handle(ReloadConfigurationCommand(config_path=config_file, graceful=True))

    assert stopped_at[0] - started < 0.5


def test_without_a_drain_a_graceful_reload_stops_at_once(config_file):
    stopped_at: list[float] = []
    handler = _handler(None, lambda: stopped_at.append(time.monotonic()))

    handler.handle(ReloadConfigurationCommand(config_path=config_file, graceful=True))

    assert stopped_at


def test_wait_idle_reports_whether_the_server_went_idle():
    concurrency = ConcurrencyManager()
    thread, _ = _call_in_flight(concurrency, hold_s=0.3)

    assert concurrency.wait_idle(SERVER, 0.05) is False
    assert concurrency.wait_idle(SERVER, 5.0) is True
    thread.join()
