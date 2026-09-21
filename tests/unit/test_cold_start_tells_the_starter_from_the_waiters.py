"""Ten callers, one start, nine waits -- and a trace that can tell them apart (#1279).

Startup has two waiting mechanisms and neither was observable per caller. Ten
concurrent calls to one cold server produced one launch and nine waits, and
every caller got the same `mcp_server.cold_start` span: the nine watching looked
exactly like the one working, so "why was this call slow" had no answer in the
trace.

These tests drive the real primitives with real threads and a barrier, never a
patched `SingleFlight`, because the thing under test IS the concurrency. They
also assert the *wiring*, in the spirit of
`test_cold_start_metrics_are_published`: a port whose adapter nothing installs
is the failure this epic exists to remove, and a mock would pass against it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from mcp_hangar.domain.contracts.startup_observer import (
    NullStartupObserver,
    get_startup_observer,
    set_startup_observer,
)
from mcp_hangar.infrastructure.single_flight import SingleFlight

WAITERS = 9


class _RecordingObserver:
    """Records every role report, with the origin each waiter was handed."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.led: list[str] = []
        self.waited: list[tuple[str, str | None]] = []
        self.origin = "00-11111111111111111111111111111111-2222222222222222-01"

    def leading(self, key: str) -> str | None:
        with self.lock:
            self.led.append(key)
        return self.origin

    @contextmanager
    def waiting(self, key: str, origin: str | None):
        with self.lock:
            self.waited.append((key, origin))
        yield


def test_one_caller_leads_and_every_other_is_recorded_as_waiting() -> None:
    """The headline count, through real threads rather than a stubbed primitive."""
    observer = _RecordingObserver()
    single_flight = SingleFlight(cache_results=False, observer=observer)
    everyone_here = threading.Barrier(WAITERS + 1)
    leader_may_finish = threading.Event()
    ran = threading.Semaphore(0)

    def work() -> str:
        ran.release()
        leader_may_finish.wait(timeout=5)
        return "started"

    results: list[str] = []
    results_lock = threading.Lock()

    def caller() -> None:
        everyone_here.wait(timeout=5)
        value = single_flight.do("math", work)
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=caller) for _ in range(WAITERS + 1)]
    for thread in threads:
        thread.start()
    # The work runs once; releasing it only after every thread is past the
    # barrier is what makes the other callers genuinely wait.
    assert ran.acquire(timeout=5), "nobody executed the work"
    leader_may_finish.set()
    for thread in threads:
        thread.join(timeout=5)

    assert results == ["started"] * (WAITERS + 1)
    assert observer.led == ["math"], f"expected exactly one leader, got {observer.led}"
    assert len(observer.waited) == WAITERS, observer.waited
    assert {key for key, _ in observer.waited} == {"math"}


def test_every_waiter_is_handed_the_leaders_origin() -> None:
    """A waiter links to the start it waited for; ADR-029 forbids a shared parent.

    The origin is carried as data -- a W3C traceparent string -- so the
    primitive never holds an SDK object and never has to know what causality
    means.
    """
    observer = _RecordingObserver()
    single_flight = SingleFlight(cache_results=False, observer=observer)
    leader_started = threading.Event()
    leader_may_finish = threading.Event()

    def work() -> str:
        leader_started.set()
        leader_may_finish.wait(timeout=5)
        return "started"

    leader = threading.Thread(target=lambda: single_flight.do("math", work))
    leader.start()
    assert leader_started.wait(timeout=5)

    waiters = [threading.Thread(target=lambda: single_flight.do("math", work)) for _ in range(3)]
    for thread in waiters:
        thread.start()
    # Give the waiters time to reach the wait before the leader finishes.
    threading.Event().wait(0.2)
    leader_may_finish.set()
    leader.join(timeout=5)
    for thread in waiters:
        thread.join(timeout=5)

    assert observer.waited, "no waiter was recorded"
    assert all(origin == observer.origin for _, origin in observer.waited), observer.waited


def test_a_failing_observer_cannot_break_a_cold_start() -> None:
    """Observation describes a start; it must never be able to stop one."""

    class _Exploding:
        def leading(self, key: str) -> str | None:
            raise RuntimeError("boom")

        def waiting(self, key: str, origin: str | None):
            raise RuntimeError("boom")

    single_flight = SingleFlight(cache_results=False, observer=_Exploding())
    leader_may_finish = threading.Event()
    leader_may_finish.set()

    assert single_flight.do("math", lambda: "started") == "started"


def test_no_observer_is_the_default_and_costs_nothing() -> None:
    assert SingleFlight().do("math", lambda: "started") == "started"


class _CountingObserver:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.waited: list[str] = []

    @contextmanager
    def starting(self, mcp_server_id: str):
        self.started.append(mcp_server_id)
        yield

    @contextmanager
    def waiting(self, mcp_server_id: str):
        self.waited.append(mcp_server_id)
        yield


@pytest.fixture
def installed_observer():
    observer = _CountingObserver()
    set_startup_observer(observer)
    yield observer
    set_startup_observer(None)


def test_the_other_waiting_mechanism_reports_its_roles_too(installed_observer, monkeypatch) -> None:
    """`ensure_ready`, the wait that single flight never sees.

    Callers that arrive after the leader has moved the server to INITIALIZING
    skip single flight entirely and wait on the aggregate's own event. That wait
    had no span at all, so it was the half of the problem that stayed invisible
    even once single flight could report.
    """
    from mcp_hangar.domain.model.mcp_server import McpServer

    server = McpServer(mcp_server_id="math", mode="subprocess", command=["true"])
    started = threading.Event()
    may_finish = threading.Event()

    def blocking_start() -> None:
        started.set()
        assert may_finish.wait(timeout=5)
        with server._lock:
            server._state = type(server.state).READY
            server._ready_event.set()

    monkeypatch.setattr(server, "_start", blocking_start)

    leader = threading.Thread(target=server.ensure_ready)
    leader.start()
    assert started.wait(timeout=5), "the starter never began"

    waiters = [threading.Thread(target=server.ensure_ready) for _ in range(3)]
    for thread in waiters:
        thread.start()
    threading.Event().wait(0.2)
    may_finish.set()
    leader.join(timeout=5)
    for thread in waiters:
        thread.join(timeout=5)

    assert installed_observer.started == ["math"], installed_observer.started
    assert installed_observer.waited == ["math"] * 3, installed_observer.waited


def test_the_aggregates_default_observer_reports_nothing() -> None:
    """The port's default is silence, so a deployment without tracing pays nothing."""
    set_startup_observer(None)
    assert isinstance(get_startup_observer(), NullStartupObserver)


def test_the_tracing_bootstrap_installs_a_real_observer(monkeypatch) -> None:
    """The wiring, not the port.

    `TracedMcpServerService` was a correct adapter that nothing constructed, and
    it survived for years because every test built it by hand. This asserts the
    boot path installs one, so the same mistake cannot repeat quietly here.
    """
    pytest.importorskip("opentelemetry.sdk")
    from mcp_hangar.server.bootstrap.observability import _install_startup_observer

    set_startup_observer(None)
    try:
        _install_startup_observer()
        installed = get_startup_observer()
        assert not isinstance(installed, NullStartupObserver)
        assert hasattr(installed, "starting") and hasattr(installed, "waiting")
    finally:
        set_startup_observer(None)
