"""The pin re-check worker: how it is built, and which servers a pass re-lists (#1693).

The served behaviour -- a drifted pinned tool refused after one pass, an
unpinned server not listed, a cold one not started -- is in
``tests/integration/test_an_unannounced_schema_change_on_a_pinned_tool_is_refused.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.pin_recheck import PIN_RECHECK_DEFAULT_INTERVAL_S, PinRecheckWorker, pin_recheck_interval_s
from mcp_hangar.server.config_schema import validate_config


def _built(monkeypatch: pytest.MonkeyPatch, config: dict[str, Any] | None) -> list[Any]:
    from mcp_hangar.server.bootstrap import workers as workers_module

    monkeypatch.setattr(workers_module, "get_runtime", lambda: SimpleNamespace(repository={}))
    return [w for w in workers_module.create_background_workers(config) if w.task == "pin_recheck"]


def test_bootstrap_builds_the_worker_by_default_without_starting_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_hangar.server.state import GROUPS

    (worker,) = _built(monkeypatch, None)
    assert worker.interval_s == PIN_RECHECK_DEFAULT_INTERVAL_S == 60
    assert worker._groups is GROUPS
    assert worker.running is False


def test_an_interval_of_zero_builds_no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _built(monkeypatch, {"tool_projection": {"pin_recheck_interval_s": 0}}) == []


def test_a_set_interval_is_the_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    (worker,) = _built(monkeypatch, {"tool_projection": {"pin_recheck_interval_s": 15}})
    assert worker.interval_s == 15


@pytest.mark.security
@pytest.mark.parametrize("value", [True, "60", 1.5, None, -1, 4, 3601])
def test_a_value_out_of_range_refuses_the_boot(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    with pytest.raises(ValueError, match="pin_recheck_interval_s"):
        _built(monkeypatch, {"tool_projection": {"pin_recheck_interval_s": value}})


def test_the_range_ends_are_accepted() -> None:
    assert pin_recheck_interval_s({"tool_projection": {"pin_recheck_interval_s": 5}}) == 5
    assert pin_recheck_interval_s({"tool_projection": {"pin_recheck_interval_s": 3600}}) == 3600


def test_the_top_level_key_is_known_to_the_schema() -> None:
    assert validate_config({"mcp_servers": {}, "tool_projection": {"pin_recheck_interval_s": 30}}) == []
    assert validate_config({"mcp_servers": {}, "tool_projection": {"pin_recheck_intervall_s": 30}}) != []


class _Server:
    def __init__(self, fail: bool = False) -> None:
        self.rechecked = 0
        self._fail = fail

    def recheck_catalogue(self) -> bool:
        self.rechecked += 1
        if self._fail:
            raise RuntimeError("boom")
        return False


class _Registry:
    def __init__(self, pins: list[tuple[str, str, str | None, Any]]) -> None:
        self._pins = pins

    def config_pins(self) -> list[tuple[str, str, str | None, Any]]:
        return self._pins

    def list_for_server(self, server_id: str) -> list[Any]:
        return []


def _pass(pins: list[tuple[str, str, str | None, Any]], servers: dict[str, _Server], groups: dict[str, Any]) -> None:
    PinRecheckWorker(servers, groups, interval_s=5, event_bus=object(), registry=lambda: _Registry(pins)).recheck_once()


def test_a_pin_on_a_group_rechecks_each_member_and_nothing_else() -> None:
    servers = {"a": _Server(), "b": _Server(), "other": _Server()}
    group = SimpleNamespace(members=[SimpleNamespace(id="a"), SimpleNamespace(id="b")])

    _pass([("g", "read_item", None, object())], servers, {"g": group})

    assert [servers[s].rechecked for s in ("a", "b", "other")] == [1, 1, 0]


def test_a_server_pinned_twice_is_rechecked_once_per_pass() -> None:
    servers = {"a": _Server()}
    pins = [("a", "read_item", None, object()), ("a", "read_item", "tenant:a", object()), ("a", "write_item", None, 1)]

    _pass(pins, servers, {})

    assert servers["a"].rechecked == 1


def test_one_failing_server_does_not_stop_the_others() -> None:
    servers = {"a": _Server(fail=True), "b": _Server()}

    _pass([("a", "read_item", None, object()), ("b", "read_item", None, object())], servers, {})

    assert [servers["a"].rechecked, servers["b"].rechecked] == [1, 1]


class _BrokenReportRegistry(_Registry):
    def list_for_server(self, server_id: str) -> list[Any]:
        if server_id == "a":
            raise RuntimeError("cannot read a's projections")
        return []


@pytest.mark.security
def test_a_server_whose_report_fails_does_not_stop_the_servers_after_it() -> None:
    servers = {"a": _Server(), "b": _Server()}
    pins = [("a", "read_item", None, object()), ("b", "read_item", None, object())]
    worker = PinRecheckWorker(
        servers, {}, interval_s=5, event_bus=object(), registry=lambda: _BrokenReportRegistry(pins)
    )

    worker.recheck_once()
    worker.recheck_once()

    assert [servers["a"].rechecked, servers["b"].rechecked] == [2, 2]


def test_a_group_whose_members_cannot_be_read_does_not_stop_the_other_pins() -> None:
    class _Broken:
        @property
        def members(self) -> list[Any]:
            raise RuntimeError("reloading")

    servers = {"b": _Server()}

    _pass([("g", "read_item", None, object()), ("b", "read_item", None, object())], servers, {"g": _Broken()})

    assert servers["b"].rechecked == 1
