"""Why a server is dead reads the same on every surface, and only while it is (#1418).

Each server is driven to DEAD down the aggregate's own failure path, with a
stand-in transport. Each surface is then read through its real handler:
`hangar_details` as `register_mcp_server_tools` registers it, `hangar_list` and
`hangar_status`, and the REST router `create_api_router` builds. The served
path, over real transports and upstream processes, is
tests/integration/test_a_dead_servers_reason_reads_the_same_on_every_surface.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
import json
from typing import Any
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.queries import register_all_handlers
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.domain.events import McpServerStateChanged
from mcp_hangar.domain.model import health_tracker
from mcp_hangar.domain.model.health_tracker import HealthTracker
from mcp_hangar.domain.model.mcp_server import (
    DEAD_CAPABILITY_BLOCKED,
    DEAD_CRASHED,
    DEAD_GIVEN_UP,
    DEAD_REASONS,
    DEAD_START_FAILED,
    DEAD_UNKNOWN,
    McpServer,
)
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.capabilities import McpServerCapabilities, ToolCapabilities
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.context import init_context, reset_context
from mcp_hangar.server.tools import mcp_server as mcp_server_tools
from mcp_hangar.server.tools.hangar import hangar_list, hangar_status

#: Text only an upstream sends. No read surface may repeat it.
SENTINEL = "SENTINEL-1418-what-the-upstream-said"


class _Upstream:
    """A stand-in connection. ``failing`` makes ``tools/list`` fail with the sentinel."""

    def __init__(self, tools: tuple[str, ...]) -> None:
        self.tools = tools
        self.failing = False
        self.alive = True
        self.closed = False
        self.modern_envelope = False

    def is_alive(self) -> bool:
        return self.alive and not self.closed

    def call(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-11-25"}}
        if method == "tools/list" and not self.failing:
            return {"result": {"tools": [{"name": name} for name in self.tools]}}
        return {"error": {"code": -32603, "message": SENTINEL}}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _server(sid: str, tools: tuple[str, ...] = ("add",), **options: Any) -> tuple[McpServer, list[_Upstream]]:
    """A server whose starts launch stand-in connections, and the list of them."""
    upstreams: list[_Upstream] = []
    server = McpServer(mcp_server_id=sid, mode="subprocess", command=["unused"], metrics_publisher=Mock(), **options)

    def launch() -> _Upstream:
        upstreams.append(_Upstream(tools))
        return upstreams[-1]

    server._create_client = launch  # type: ignore[method-assign]
    return server, upstreams


def _given_up() -> McpServer:
    """Health checks fail and degrade it, and the recovery saga gives up."""
    server, upstreams = _server("gave-up", max_consecutive_failures=1)
    server.ensure_ready()
    upstreams[-1].failing = True
    while server.state is not McpServerState.DEGRADED:
        server.health_check()
    assert server.give_up("max_retries_exceeded") is True
    return server


def _crashed() -> McpServer:
    """Its process dies between two requests, and a health check notices."""
    server, upstreams = _server("crashed")
    server.ensure_ready()
    upstreams[-1].alive = False
    server.health_check()
    return server


def _start_failed() -> McpServer:
    """A start fails, below the failures that would degrade it."""
    server, _ = _server("start-failed")

    def broken() -> Any:
        raise OSError(SENTINEL)

    server._create_client = broken  # type: ignore[method-assign]
    with pytest.raises(Exception, match=SENTINEL):  # the text is there to leak
        server.ensure_ready()
    return server


def _capability_blocked() -> McpServer:
    """Block mode finds a tool outside `expected_tools` on start."""
    server, _ = _server(
        "blocked",
        tools=("add", "exfiltrate"),
        capabilities=McpServerCapabilities(tools=ToolCapabilities(expected_tools=("add",)), enforcement_mode="block"),
    )
    with pytest.raises(Exception):  # noqa: B017 -- the start fails; how is tested elsewhere
        server.ensure_ready()
    return server


DRIVERS: dict[str, Callable[[], McpServer]] = {
    DEAD_GIVEN_UP: _given_up,
    DEAD_CRASHED: _crashed,
    DEAD_START_FAILED: _start_failed,
    DEAD_CAPABILITY_BLOCKED: _capability_blocked,
}


@pytest.fixture(autouse=True)
def fresh_context() -> Iterator[None]:
    reset_context()
    yield
    reset_context()


def _serve(*servers: McpServer) -> None:
    """A context whose repository holds ``servers``, with the real query handlers."""
    repository = InMemoryMcpServerRepository()
    for server in servers:
        repository.add(server.mcp_server_id, server)
    query_bus = QueryBus()
    init_context(create_runtime(repository=repository, query_bus=query_bus))
    register_all_handlers(query_bus, repository, event_store=InMemoryEventStore())


def _hangar_details() -> Callable[[str], dict[str, Any]]:
    """`hangar_details` as `register_mcp_server_tools` registers it."""
    registered: dict[str, Any] = {}

    class _Mcp:
        def tool(self, name: str | None = None, **_kwargs: Any) -> Any:
            def register(fn: Any) -> Any:
                registered[name or fn.__name__] = fn
                return fn

            return register

    mcp_server_tools.register_mcp_server_tools(_Mcp())
    return registered["hangar_details"]


def _read(sid: str) -> tuple[dict[str, dict[str, Any]], list[Any]]:
    """Each surface's entry for ``sid``, and every whole answer read."""
    rest = TestClient(create_api_router(auth_components=None))
    details = rest.get(f"/mcp_servers/{sid}")
    listed = rest.get("/mcp_servers")
    assert (details.status_code, listed.status_code) == (200, 200)
    tool_details = _hangar_details()(sid)
    tool_list = hangar_list()
    status = hangar_status()
    entries = {
        "GET /api/mcp_servers/{id}": details.json(),
        "GET /api/mcp_servers": next(e for e in listed.json()["mcp_servers"] if e["mcp_server_id"] == sid),
        "hangar_details": tool_details,
        "hangar_list": next(e for e in tool_list["mcp_servers"] if e["mcp_server_id"] == sid),
        "hangar_status": next(e for e in status["mcp_servers"] if e["id"] == sid),
    }
    return entries, [details.text, listed.text, tool_details, tool_list, status]


@pytest.mark.parametrize("reason", sorted(DRIVERS))
def test_each_reason_reads_the_same_on_every_surface(reason: str) -> None:
    server = DRIVERS[reason]()
    assert (server.state, server.dead_reason_snapshot) == (McpServerState.DEAD, reason)
    _serve(server)

    entries, _ = _read(server.mcp_server_id)

    dead = entries["hangar_details"]["dead"]
    assert {surface: entry["dead"] for surface, entry in entries.items()} == dict.fromkeys(entries, dead)
    assert {surface: entry["state"] for surface, entry in entries.items()} == dict.fromkeys(entries, "dead")
    assert dead["reason"] == reason
    assert dead["revived_by"] == ("start" if reason == DEAD_CAPABILITY_BLOCKED else "call_or_start")
    status = server.dead_status()
    assert status is not None
    assert datetime.fromisoformat(dead["since"]).timestamp() == pytest.approx(status.since)
    if reason == DEAD_CAPABILITY_BLOCKED or server.health.last_failure_at is None:
        assert dead["retry_allowed_at"] is None
    else:
        assert datetime.fromisoformat(dead["retry_allowed_at"]).timestamp() == pytest.approx(
            server.health.backoff_ends_by()
        )
    only_a_start = "only hangar_start starts it again"
    expected_note = only_a_start if reason == DEAD_CAPABILITY_BLOCKED else "a call after its backoff"
    assert f"Failed ({reason}): " in entries["hangar_status"]["note"]
    assert expected_note in entries["hangar_status"]["note"]


def test_every_reason_is_driven() -> None:
    assert set(DRIVERS) == DEAD_REASONS


@pytest.mark.parametrize("state", ["cold", "ready"])
def test_a_server_that_is_not_dead_reads_null_on_every_surface(state: str) -> None:
    server, _ = _server("alive")
    if state == "ready":
        server.ensure_ready()
    _serve(server)

    entries, _ = _read("alive")

    assert {surface: entry["dead"] for surface, entry in entries.items()} == dict.fromkeys(entries)
    assert server.dead_status() is None


def test_a_revived_server_reads_null_again() -> None:
    server = _crashed()
    server.ensure_ready()
    _serve(server)

    entries, _ = _read("crashed")

    assert {surface: entry["dead"] for surface, entry in entries.items()} == dict.fromkeys(entries)


def test_no_surface_repeats_what_the_upstream_said() -> None:
    servers = [driver() for driver in DRIVERS.values()]
    _serve(*servers)

    answers = [answer for server in servers for answer in _read(server.mcp_server_id)[1]]

    assert SENTINEL not in json.dumps(answers)


@pytest.mark.parametrize("recorded", [None, SENTINEL, "given_up_later"])
def test_a_restored_server_reports_a_bounded_reason_and_the_recorded_time(recorded: str | None) -> None:
    server, _ = _server("restored")
    event = McpServerStateChanged(mcp_server_id="restored", old_state="ready", new_state="dead", dead_reason=recorded)
    server._replay_state_changed(event)

    status = server.dead_status()

    assert status is not None
    assert (status.reason, status.since, status.revived_by_call) == (DEAD_UNKNOWN, event.occurred_at, True)


def test_the_backoff_ceiling_lets_every_retry_through_whatever_the_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = HealthTracker(jitter_factor=0.5)
    assert tracker.backoff_ends_by() is None
    for _ in range(3):
        tracker.record_failure()
    ends_by = tracker.backoff_ends_by()
    assert ends_by is not None

    monkeypatch.setattr(health_tracker.time, "time", lambda: ends_by)
    assert all(tracker.can_retry() for _ in range(500))
    # Inside the shortest backoff any draw gives: 2**3 seconds, less half.
    failed_at = tracker.last_failure_at
    assert failed_at is not None
    monkeypatch.setattr(health_tracker.time, "time", lambda: failed_at + 8.0 * 0.5 - 0.01)
    assert not any(tracker.can_retry() for _ in range(500))
