"""What `ReloadWarmUp` starts after a reload, and what it leaves alone.

The served behaviour, over streamable HTTP with real subprocess servers, is in
``tests/integration/test_a_server_a_reload_adds_is_served_on_the_front_door.py``.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from mcp_hangar.domain.events import ConfigurationReloaded
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.server.bootstrap.reload_warm_up import ReloadWarmUp


def _reloaded(added: list[str], updated: tuple[str, ...] = ()) -> ConfigurationReloaded:
    return ConfigurationReloaded(
        config_path="config.yaml",
        mcp_servers_added=added,
        mcp_servers_removed=["gone"],
        mcp_servers_updated=list(updated),
        mcp_servers_unchanged=["kept"],
        reload_duration_ms=1.0,
        requested_by="test",
    )


def _runtime(states: dict[str, str], fail: frozenset[str] = frozenset()) -> tuple[Any, list[str]]:
    sent: list[str] = []

    def send(command: Any) -> None:
        sent.append(command.mcp_server_id)
        if command.mcp_server_id in fail:
            raise RuntimeError("upstream printed a secret")

    def get(mcp_server_id: str) -> Any:
        state = states.get(mcp_server_id)
        return None if state is None else SimpleNamespace(state=SimpleNamespace(value=state))

    runtime = SimpleNamespace(repository=SimpleNamespace(get=get), command_bus=SimpleNamespace(send=send))
    return runtime, sent


@pytest.fixture
def mode():
    reset_tool_access_resolver()

    def set_mode(value: str) -> None:
        get_tool_access_resolver().set_topology_mode(value)

    yield set_mode
    reset_tool_access_resolver()


def test_the_front_door_starts_the_added_and_updated_servers_and_nothing_else(mode: Any) -> None:
    mode("front_door")
    runtime, sent = _runtime({"late": "cold", "edit": "cold", "kept": "ready"})

    ReloadWarmUp(runtime).warm(["late", "edit"])

    assert sent == ["late", "edit"]


def test_the_handler_starts_them_on_a_thread_of_its_own(mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    mode("front_door")
    runtime, _sent = _runtime({"late": "cold", "edit": "cold"})
    warm_up = ReloadWarmUp(runtime)
    seen: list[tuple[list[str], str]] = []
    finished = threading.Event()

    def record(ids: list[str]) -> None:
        seen.append((ids, threading.current_thread().name))
        finished.set()

    monkeypatch.setattr(warm_up, "warm", record)
    warm_up.handle(_reloaded(["late"], ("edit",)))

    assert finished.wait(5)
    assert seen == [(["late", "edit"], "mcp-hangar-reload-warmup")]


def test_egress_starts_nothing(mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    mode("egress")
    runtime, _sent = _runtime({"late": "cold"})
    warm_up = ReloadWarmUp(runtime)
    called: list[Any] = []
    monkeypatch.setattr(warm_up, "warm", called.append)

    warm_up.handle(_reloaded(["late"]))

    assert called == []


def test_a_dead_or_vanished_server_is_skipped_and_a_failed_start_costs_the_others_nothing(mode: Any) -> None:
    mode("front_door")
    runtime, sent = _runtime({"dead": "dead", "broken": "cold", "late": "cold"}, fail=frozenset({"broken"}))

    with capture_logs() as logs:
        ReloadWarmUp(runtime).warm(["vanished", "dead", "broken", "late"])

    assert sent == ["broken", "late"]
    (failed,) = [line for line in logs if line["event"] == "front_door_reload_warmup_failed"]
    assert failed["mcp_server_id"] == "broken"
    assert failed["error_type"] == "RuntimeError"
    assert "secret" not in str(failed)
    (complete,) = [line for line in logs if line["event"] == "front_door_reload_warmup_complete"]
    assert (complete["warmed"], complete["failed"], complete["skipped"]) == (1, 1, 2)
