"""The required-catalogue retry runs even when the front door's warm-up hangs (#2128).

`CatalogueRetry` (#1446) ran on the warm-up's thread, after the warm-up. A
warm-up that hung on one server, say on a handshake that never returns, held it
back for as long as it hung: a required server that had failed was never
retried, and readiness stayed 503. The warm-up is bounded (#2122); once that
bound passes, the retry now starts on a thread of its own. A warm-up that
finishes in time still runs it on its own thread, as before, and shutdown still
stops it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.fastmcp_server import catalogue_warmup
from mcp_hangar.server import catalogue_readiness, lifecycle
from mcp_hangar.server.catalogue_readiness import (
    CatalogueRetry,
    RequiredCatalogue,
    Requirement,
    configure_required_catalogue,
)
from mcp_hangar.server.lifecycle import ServerLifecycle

#: Short enough for a unit test, and never zero: a busy loop would hide a missing wait.
FAST = {"poll_s": 0.01, "spacing_s": 0.01}
BOUND_S = 0.2
WARM_UP_THREAD = "mcp-hangar-front-door-warmup"
RETRY_THREAD = "mcp-hangar-catalogue-retry"


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    catalogue_readiness.reset()
    catalogue_warmup.reset()
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    catalogue_readiness.reset()
    catalogue_warmup.reset()
    reset_tool_projection_registry()
    reset_tool_access_resolver()


class _Fleet:
    """`hung` blocks in its start until released; `payments` fails its first *failures* starts."""

    def __init__(self, order: list[str], *, failures: int) -> None:
        self.order = order
        self.failures = failures
        self.release = threading.Event()
        self.servers = {server_id: SimpleNamespace(state=SimpleNamespace(value="cold")) for server_id in order}
        self.sent: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self.repository = SimpleNamespace(get_all_ids=lambda: list(self.order), get=self.servers.get)
        self.command_bus = SimpleNamespace(send=self.send)

    def send(self, command: Any) -> None:
        server_id = command.mcp_server_id
        with self._lock:
            self.sent.append((server_id, threading.current_thread().name))
        server = self.servers[server_id]
        if server_id == "hung":
            # As `ensure_ready` would: the start is under way while it waits.
            server.state.value = "initializing"
            self.release.wait(timeout=10)
        elif server_id == "payments":
            with self._lock:
                fail = self.starts("payments") <= self.failures
            if fail:
                raise RuntimeError("down")
        server.state.value = "ready"
        get_tool_projection_registry().build_from_tools(server_id, [])

    def starts(self, server_id: str) -> int:
        return sum(1 for sent_id, _thread in self.sent if sent_id == server_id)

    def threads(self, server_id: str) -> set[str]:
        return {thread for sent_id, thread in self.sent if sent_id == server_id}


@pytest.fixture
def runtime_for() -> Iterator[Callable[..., _Fleet]]:
    """A front door that requires `payments`, with the warm-up's bound set to *bound_s*."""
    fleets: list[_Fleet] = []
    stack: list[Any] = []

    def make(order: list[str], *, failures: int = 1, bound_s: float = BOUND_S) -> _Fleet:
        get_tool_access_resolver().set_topology_mode("front_door")
        configure_required_catalogue(
            RequiredCatalogue(requirements=(Requirement(name="payments", servers=("payments",)),), retry_for_s=600.0)
        )
        bound = patch.object(lifecycle, "warm_up_bound_s", return_value=bound_s)
        bound.start()
        stack.append(bound)
        fleet = _Fleet(order, failures=failures)
        fleets.append(fleet)
        return fleet

    yield make
    for fleet in fleets:
        fleet.release.set()
    for bound in stack:
        bound.stop()


def _until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def _ready(fleet: _Fleet) -> bool:
    """Whether the required catalogue still holds `/health/ready` at 503."""
    report = catalogue_readiness.catalogue_readiness(fleet.repository)
    return report is not None and not report["holds_readiness"]


def _retry_state() -> str:
    return catalogue_readiness._gate._retry


def test_a_failed_required_server_is_retried_while_the_warm_up_hangs(runtime_for: Callable[..., _Fleet]) -> None:
    fleet = runtime_for(["payments", "hung"])
    retry = CatalogueRetry(fleet, **FAST)
    try:
        warm_up = lifecycle.start_front_door_warm_up(fleet, retry)

        assert _until(lambda: fleet.starts("hung") == 1), "the warm-up never reached the hung server"
        assert fleet.starts("payments") == 1
        assert not _ready(fleet)

        assert _until(lambda: _ready(fleet)), "the failed required server was never retried"
        assert fleet.starts("payments") == 2
        assert fleet.threads("payments") == {WARM_UP_THREAD, RETRY_THREAD}
        assert warm_up.is_alive(), "the warm-up was meant to be hanging still"
        # The server the warm-up hangs on is `initializing`: the retry leaves it alone.
        assert fleet.starts("hung") == 1
        assert _until(lambda: _retry_state() == "finished")
    finally:
        retry.stop()
        fleet.release.set()
    warm_up.join(5.0)
    assert not warm_up.is_alive()
    # The warm-up that finished late does not run the retry a second time.
    assert fleet.starts("payments") == 2


def test_a_required_server_the_hung_warm_up_never_reached_is_started(runtime_for: Callable[..., _Fleet]) -> None:
    fleet = runtime_for(["hung", "payments"], failures=0)
    retry = CatalogueRetry(fleet, **FAST)
    try:
        warm_up = lifecycle.start_front_door_warm_up(fleet, retry)

        assert _until(lambda: _ready(fleet)), "a required server behind the hung one was never started"
        assert fleet.threads("payments") == {RETRY_THREAD}
        assert fleet.starts("hung") == 1
    finally:
        retry.stop()
        fleet.release.set()
    warm_up.join(5.0)
    assert not warm_up.is_alive()


def test_a_warm_up_that_finishes_in_time_runs_the_retry_on_its_own_thread(runtime_for: Callable[..., _Fleet]) -> None:
    fleet = runtime_for(["payments"], bound_s=10.0)
    retry = CatalogueRetry(fleet, **FAST)
    try:
        warm_up = lifecycle.start_front_door_warm_up(fleet, retry)
        warm_up.join(5.0)
        assert not warm_up.is_alive()
    finally:
        retry.stop()

    assert _ready(fleet)
    assert fleet.starts("payments") == 2
    assert fleet.threads("payments") == {WARM_UP_THREAD}
    assert _retry_state() == "finished"
    assert not [thread for thread in threading.enumerate() if thread.name == RETRY_THREAD]


def _lifecycle(fleet: _Fleet) -> ServerLifecycle:
    context = MagicMock()
    context.runtime = fleet
    context.discovery_orchestrator = None
    server_lifecycle = ServerLifecycle(context)
    server_lifecycle._catalogue_retry = CatalogueRetry(fleet, **FAST)
    return server_lifecycle


def test_shutdown_stops_a_retry_that_runs_past_the_bound(runtime_for: Callable[..., _Fleet]) -> None:
    fleet = runtime_for(["payments", "hung"], failures=10_000)
    server_lifecycle = _lifecycle(fleet)
    try:
        warm_up = lifecycle.start_front_door_warm_up(fleet, server_lifecycle._catalogue_retry)
        assert _until(lambda: fleet.starts("payments") >= 3), "the retry never ran past the bound"

        with patch.object(lifecycle, "stop_coordination"):
            server_lifecycle.shutdown()

        assert not [thread for thread in threading.enumerate() if thread.name == RETRY_THREAD]
        stopped_at = fleet.starts("payments")
        time.sleep(0.1)
        assert fleet.starts("payments") == stopped_at
        assert _retry_state() == "stopped"
    finally:
        server_lifecycle._catalogue_retry.stop()
        fleet.release.set()
    warm_up.join(5.0)


def test_shutdown_before_the_bound_passes_leaves_nothing_to_retry(runtime_for: Callable[..., _Fleet]) -> None:
    fleet = runtime_for(["payments", "hung"])
    server_lifecycle = _lifecycle(fleet)
    try:
        warm_up = lifecycle.start_front_door_warm_up(fleet, server_lifecycle._catalogue_retry)
        assert _until(lambda: fleet.starts("hung") == 1)

        with patch.object(lifecycle, "stop_coordination"):
            server_lifecycle.shutdown()
        time.sleep(BOUND_S + 0.2)

        assert not catalogue_warmup.is_warming(), "the bound was meant to have passed"
        assert fleet.starts("payments") == 1
        assert _retry_state() == "not_started"
    finally:
        fleet.release.set()
    warm_up.join(5.0)
