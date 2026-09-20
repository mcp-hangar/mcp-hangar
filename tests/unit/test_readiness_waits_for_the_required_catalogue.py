"""A front-door replica waits, within a window, for its required catalogue (#1446).

The decision, the config check and the retry's choices, without processes. The
served path -- a real `bootstrap()`, `ServerLifecycle.start`, real upstreams
that are down at boot, given up on, capability-blocked or idle-stopped -- is
`tests/integration/test_a_front_door_is_ready_once_its_catalogue_is_projected.py`,
and at default settings
`tests/integration/test_readiness_falls_back_after_the_retry_window_at_default_settings.py`.

What the retry must never do is what #1429's reconciler did: start a server
deliberately, so that backoff and the dead reasons were skipped, and start it
again forever, idle-stopped ones included. What readiness must never do is wait
for good: the window bounds it.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from structlog.testing import capture_logs

from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.application.commands.handlers import StartMcpServerHandler
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.exceptions import CannotStartMcpServerError, ConfigurationError
from mcp_hangar.domain.model.mcp_server import DEAD_CAPABILITY_BLOCKED
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.metrics import get_metrics
from mcp_hangar.server import catalogue_readiness
from mcp_hangar.server.bootstrap import ApplicationContext
from mcp_hangar.server.catalogue_readiness import (
    CatalogueRetry,
    RequiredCatalogue,
    Requirement,
    configure_required_catalogue,
    required_catalogue,
)
from mcp_hangar.server.config import apply_process_config, load_configuration
from mcp_hangar.server.config_schema import validate_config
from mcp_hangar.server.lifecycle import ServerLifecycle, build_readiness_report
from tests.unit.test_a_given_up_server_reads_dead import _Fleet, _pipeline

#: The package, not the `bootstrap` function `mcp_hangar.server` re-exports under the same name.
bootstrap_package = importlib.import_module("mcp_hangar.server.bootstrap")

SPEC = {"mode": "subprocess", "command": [sys.executable, "-c", "pass"]}
REMOTE = {"mode": "remote", "endpoint": "http://127.0.0.1:9/mcp"}
#: Short enough for a unit test, and never zero: a busy loop would hide a missing wait.
FAST = {"poll_s": 0.01, "spacing_s": 0.0}
#: A window a unit test can outlive.
SHORT_WINDOW_S = 0.2


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    catalogue_readiness.reset()
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    catalogue_readiness.reset()
    reset_tool_projection_registry()
    reset_tool_access_resolver()


def _config(*names: str, **block: Any) -> dict[str, Any]:
    return {
        "mcp_servers": {
            "payments": dict(SPEC),
            "search": dict(SPEC),
            "pool": {"mode": "group", "members": [{"id": "pool-a"}, {"id": "pool-b"}]},
        },
        "tool_access": {"mode": "front_door", "required_catalogue": {"servers": list(names), **block}},
    }


def _required(*requirements: Requirement, retry_for_s: float = 600.0) -> RequiredCatalogue:
    return RequiredCatalogue(requirements=requirements, retry_for_s=retry_for_s)


def _server_req(name: str) -> Requirement:
    return Requirement(name=name, servers=(name,))


def _project(server_id: str) -> None:
    get_tool_projection_registry().build_from_tools(server_id, [])


def _outlive_the_window() -> None:
    time.sleep(SHORT_WINDOW_S + 0.05)


# ----------------------------------------------------------------------------
# The configuration
# ----------------------------------------------------------------------------


class TestTheConfiguration:
    def test_absent_is_none(self) -> None:
        assert required_catalogue({"mcp_servers": {"payments": SPEC}, "tool_access": {"mode": "front_door"}}) is None

    def test_a_list_is_read_in_order_once_each_with_the_default_window(self) -> None:
        required = required_catalogue(_config("search", "payments", "search"))

        assert required == _required(_server_req("search"), _server_req("payments"))
        assert required.retry_for_s == catalogue_readiness.DEFAULT_RETRY_FOR_S

    def test_an_unknown_name_is_refused_naming_it(self) -> None:
        with pytest.raises(ConfigurationError, match="'paymnets', which is not a server or group in mcp_servers"):
            required_catalogue(_config("payments", "paymnets"))

    def test_a_group_is_satisfied_by_any_one_member(self) -> None:
        assert required_catalogue(_config("pool")) == _required(Requirement(name="pool", servers=("pool-a", "pool-b")))

    def test_a_group_member_defined_only_inline_can_be_required(self) -> None:
        config = _config("pool-c")
        config["mcp_servers"]["pool"]["members"].append({"id": "pool-c", **REMOTE})

        assert required_catalogue(config) == _required(_server_req("pool-c"))

    def test_a_group_with_no_members_is_refused(self) -> None:
        config = _config("pool")
        config["mcp_servers"]["pool"]["members"] = []

        with pytest.raises(ConfigurationError, match="group 'pool', which has no members"):
            required_catalogue(config)

    @pytest.mark.parametrize(
        "block",
        [
            ["payments"],
            {"servers": "payments"},
            {"servers": []},
            {"servers": ["payments", 3]},
            {"servers": ["payments"], "retry_for": 60},
            {"servers": ["payments"], "retry_for_s": 0},
            {"servers": ["payments"], "retry_for_s": -1},
            {"servers": ["payments"], "retry_for_s": True},
            {"servers": ["payments"], "retry_for_s": "10m"},
        ],
    )
    def test_a_malformed_block_is_refused(self, block: Any) -> None:
        config = _config()
        config["tool_access"]["required_catalogue"] = block

        with pytest.raises(ConfigurationError, match="tool_access.required_catalogue"):
            required_catalogue(config)

    def test_the_schema_knows_the_key(self) -> None:
        assert validate_config(_config("payments")) == []

    def test_an_unknown_name_is_refused_from_a_file_and_from_a_dict(self, tmp_path) -> None:
        # One check, on both paths (#1415): a dict is held to what a file is.
        config = _config("payments", "paymnets")
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config))

        with pytest.raises(ConfigurationError, match="paymnets"):
            load_configuration(str(path), load_servers=False)
        with pytest.raises(ConfigurationError, match="paymnets"):
            bootstrap_package._read_configuration(None, config)

    def test_a_file_and_a_dict_put_the_same_list_in_force(self, tmp_path) -> None:
        config = _config("payments", "pool", retry_for_s=30)
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config))

        load_configuration(str(path), load_servers=False)
        from_file = catalogue_readiness._gate.required()
        catalogue_readiness.reset()
        bootstrap_package._read_configuration(None, config)
        from_dict = catalogue_readiness._gate.required()

        assert from_file == from_dict == required_catalogue(config)

    def test_egress_checks_it_and_does_not_apply_it(self) -> None:
        config = _config("payments")
        del config["tool_access"]["mode"]

        apply_process_config(config)

        assert get_tool_access_resolver().topology_mode == "egress"
        assert catalogue_readiness._gate.required() is None
        with pytest.raises(ConfigurationError, match="paymnets"):
            apply_process_config({**config, "tool_access": {"required_catalogue": {"servers": ["paymnets"]}}})


#: Where several replicas take the management lease, and only its holder may start a local-mode server.
LEASED = {
    "coordination": {"coordination": {"lease_ttl_s": 30}},
    "shared storage": {"persistence": {"backend": "postgresql"}},
}


class TestWhereOnlyTheLeaseHolderStartsLocalModes:
    @pytest.mark.parametrize("where", sorted(LEASED))
    @pytest.mark.parametrize("mode", ["subprocess", "docker", "container", "podman", None])
    def test_a_required_local_mode_server_is_refused(self, where: str, mode: str | None) -> None:
        config = {**_config("payments"), **LEASED[where]}
        spec = {"command": ["unused"]} if mode is None else {"mode": mode, "command": ["unused"]}
        config["mcp_servers"]["payments"] = spec

        with pytest.raises(ConfigurationError, match=r"requires \['payments'\], which run in a local mode"):
            required_catalogue(config)

    @pytest.mark.parametrize("where", sorted(LEASED))
    def test_it_is_refused_from_a_file_and_from_a_dict(self, where: str, tmp_path) -> None:
        config = {**_config("payments"), **LEASED[where]}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config))

        with pytest.raises(ConfigurationError, match="local mode"):
            load_configuration(str(path), load_servers=False)
        with pytest.raises(ConfigurationError, match="local mode"):
            bootstrap_package._read_configuration(None, config)

    @pytest.mark.parametrize("where", sorted(LEASED))
    def test_a_remote_server_or_a_group_with_a_remote_member_is_accepted(self, where: str) -> None:
        config = {**_config("payments", "pool"), **LEASED[where]}
        config["mcp_servers"]["payments"] = dict(REMOTE)
        config["mcp_servers"]["pool"]["members"].append({"id": "pool-c", **REMOTE})

        assert required_catalogue(config) is not None

    def test_a_group_whose_members_are_all_local_is_refused(self) -> None:
        config = {**_config("pool"), **LEASED["shared storage"]}
        config["mcp_servers"]["pool"]["members"] = [{"id": "pool-a", **SPEC}]

        with pytest.raises(ConfigurationError, match=r"requires \['pool'\]"):
            required_catalogue(config)

    def test_a_backend_local_to_one_process_takes_no_lease_and_is_accepted(self) -> None:
        assert required_catalogue({**_config("payments"), "persistence": {"backend": "sqlite"}}) is not None


# ----------------------------------------------------------------------------
# What /health/ready says
# ----------------------------------------------------------------------------


def _server(state: str, dead_reason: str | None = None, *, can_retry: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(value=state),
        dead_reason_snapshot=dead_reason,
        health=SimpleNamespace(can_retry=lambda: can_retry),
    )


class _Repository:
    def __init__(self, **servers: SimpleNamespace) -> None:
        self.servers = servers

    def get(self, server_id: str) -> SimpleNamespace | None:
        return self.servers.get(server_id)

    def get_all(self) -> dict[str, SimpleNamespace]:
        return self.servers

    def count(self) -> int:
        return len(self.servers)


def _counts(**fields: Any) -> dict[str, Any]:
    """The readiness body's `catalogue` field: counts and state only."""
    return {
        "complete": False,
        "holds_readiness": True,
        "required": 1,
        "projected": 0,
        "missing_count": 1,
        "not_retried_count": 0,
        "retry": "not_started",
        **fields,
    }


class TestReadiness:
    def test_no_list_changes_nothing(self) -> None:
        body, status = build_readiness_report(_Repository(payments=_server("cold")))

        assert status == 200
        assert "catalogue" not in body

    def test_it_waits_until_the_list_is_projected_and_counts_what_is_missing(self) -> None:
        configure_required_catalogue(_required(_server_req("payments"), _server_req("search")))
        repository = _Repository(payments=_server("ready"), search=_server("dead", "start_failed"))
        _project("payments")

        body, status = build_readiness_report(repository)

        assert status == 503
        assert body["status"] == "unhealthy"
        assert body["catalogue"] == _counts(required=2, projected=1)

        _project("search")
        body, status = build_readiness_report(repository)

        assert status == 200
        assert body["catalogue"] == _counts(
            complete=True, holds_readiness=False, required=2, projected=2, missing_count=0
        )

    def test_the_unauthenticated_body_names_no_server_and_no_dead_reason(self) -> None:
        # `/health/ready` is on the auth skip-list: whoever can reach the port reads it.
        configure_required_catalogue(_required(_server_req("payments"), _server_req("search")))
        repository = _Repository(payments=_server("dead", "capability_blocked"), search=_server("dead", "given_up"))

        body, status = build_readiness_report(repository)

        assert status == 503
        assert body["catalogue"] == _counts(required=2, missing_count=2, not_retried_count=2)
        text = json.dumps(body)
        for word in ("payments", "search", "capability_blocked", "given_up"):
            assert word not in text

    def test_once_met_a_cold_fleet_and_a_new_list_leave_it_ready(self) -> None:
        # The #599 deadlock is "the last backend went idle -> 503". A projection
        # outlives a stop, and meeting the list is a latch, so neither can happen.
        configure_required_catalogue(_required(_server_req("payments")))
        _project("payments")
        assert build_readiness_report(_Repository(payments=_server("ready")))[1] == 200

        configure_required_catalogue(_required(_server_req("search")))  # a reload, inside the window
        body, status = build_readiness_report(_Repository(payments=_server("cold"), search=_server("dead", "crashed")))

        assert status == 200
        assert body["catalogue"] == _counts(holds_readiness=False), "reported, and not held"

    def test_one_member_satisfies_a_group(self) -> None:
        configure_required_catalogue(_required(Requirement(name="pool", servers=("pool-a", "pool-b"))))
        repository = _Repository(**{"pool-a": _server("dead", "start_failed"), "pool-b": _server("cold")})
        assert build_readiness_report(repository)[0]["catalogue"] == _counts()

        _project("pool-b")

        assert build_readiness_report(repository)[1] == 200


class TestTheWindow:
    def test_readiness_falls_back_once_the_window_ends(self) -> None:
        # The blocker: a required server the saga gave up on is never started
        # again, so without the window readiness would read 503 for good.
        configure_required_catalogue(_required(_server_req("payments"), retry_for_s=SHORT_WINDOW_S))
        repository = _Repository(payments=_server("dead", "given_up"))
        assert build_readiness_report(repository)[1] == 503

        _outlive_the_window()
        body, status = build_readiness_report(repository)

        assert status == 200
        assert body["status"] == "healthy"
        assert body["catalogue"] == _counts(holds_readiness=False, not_retried_count=1)

    def test_the_window_opens_with_the_first_configuration_and_a_reload_does_not_move_it(self) -> None:
        configure_required_catalogue(_required(_server_req("payments"), retry_for_s=SHORT_WINDOW_S))
        _outlive_the_window()

        configure_required_catalogue(_required(_server_req("payments"), retry_for_s=600))  # a reload

        assert build_readiness_report(_Repository(payments=_server("cold")))[1] == 200

    def test_a_list_a_reload_adds_to_a_replica_that_booted_without_one_is_reported_not_held(self) -> None:
        configure_required_catalogue(None)  # booted without one: the window is already over
        configure_required_catalogue(_required(_server_req("payments")))

        body, status = build_readiness_report(_Repository(payments=_server("cold")))

        assert status == 200
        assert body["catalogue"] == _counts(holds_readiness=False)

    def test_a_reload_that_removes_the_list_releases_the_wait(self) -> None:
        configure_required_catalogue(_required(_server_req("payments")))
        assert build_readiness_report(_Repository(payments=_server("cold")))[1] == 503

        configure_required_catalogue(None)

        body, status = build_readiness_report(_Repository(payments=_server("cold")))
        assert status == 200
        assert "catalogue" not in body

    def test_a_reload_that_removes_the_list_clears_it_once_met_too(self) -> None:
        configure_required_catalogue(_required(_server_req("payments")))
        _project("payments")
        assert build_readiness_report(_Repository(payments=_server("ready")))[0]["catalogue"]["complete"] is True

        configure_required_catalogue(None)

        assert "catalogue" not in build_readiness_report(_Repository(payments=_server("ready")))[0]
        assert catalogue_readiness.catalogue_detail(_Repository()) is None

    def test_the_retry_reads_final_the_moment_the_window_ends(self) -> None:
        # Not a poll later: the retry notices on its next pass, and starts
        # nothing in between, so readiness does not report it `running`.
        configure_required_catalogue(_required(_server_req("payments"), retry_for_s=SHORT_WINDOW_S))
        catalogue_readiness._gate.set_retry("running")
        repository = _Repository(payments=_server("dead", "start_failed"))
        assert build_readiness_report(repository)[0]["catalogue"]["retry"] == "running"

        _outlive_the_window()

        assert build_readiness_report(repository)[0]["catalogue"]["retry"] == "exhausted"

    def test_the_retry_reads_blocked_the_moment_nothing_is_left_it_may_start(self) -> None:
        configure_required_catalogue(_required(_server_req("payments"), _server_req("search")))
        catalogue_readiness._gate.set_retry("running")
        repository = _Repository(payments=_server("dead", "given_up"), search=_server("dead", "capability_blocked"))

        body, status = build_readiness_report(repository)

        assert (status, body["catalogue"]["retry"]) == (503, "blocked")


class TestWhereTheNamesGo:
    """The ids and dead reasons the readiness body leaves out, on the surfaces operators already use."""

    def _fleet(self, retry_for_s: float = 600.0) -> _Repository:
        configure_required_catalogue(_required(_server_req("payments"), _server_req("search"), retry_for_s=retry_for_s))
        return _Repository(payments=_server("dead", "capability_blocked"), search=_server("dead", "start_failed"))

    def test_the_log_names_what_is_missing_and_why_once_per_change(self) -> None:
        repository = self._fleet()

        with capture_logs() as logs:
            build_readiness_report(repository)
            build_readiness_report(repository)  # a probe a few seconds later: nothing new
            _project("search")
            build_readiness_report(repository)

        waiting = [entry for entry in logs if entry["event"] == "required_catalogue_waiting"]
        assert [(entry["missing"], entry["not_retried"], entry["holds_readiness"]) for entry in waiting] == [
            (["payments", "search"], {"payments": "capability_blocked"}, True),
            (["payments"], {"payments": "capability_blocked"}, True),
        ]

    def test_they_are_still_logged_after_the_window_ends(self) -> None:
        repository = self._fleet(retry_for_s=SHORT_WINDOW_S)
        build_readiness_report(repository)
        _outlive_the_window()

        with capture_logs() as logs:
            build_readiness_report(repository)

        [entry] = [entry for entry in logs if entry["event"] == "required_catalogue_waiting"]
        assert (entry["missing"], entry["holds_readiness"]) == (["payments", "search"], False)

    def test_hangar_health_names_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_hangar.server.tools import health

        repository = self._fleet()
        view = SimpleNamespace(
            groups=[], total_servers=2, servers_by_state=lambda: {"dead": 2}, scope_fields=lambda: {}
        )
        context = SimpleNamespace(rate_limiter=SimpleNamespace(get_stats=lambda: {}), repository=repository)
        monkeypatch.setattr(health, "observe_replica", lambda: view)
        monkeypatch.setattr(health, "get_context", lambda: context)

        assert health.hangar_health()["catalogue"] == {
            "complete": False,
            "holds_readiness": True,
            "required": ["payments", "search"],
            "missing": ["payments", "search"],
            "not_retried": {"payments": "capability_blocked"},
            "retry": "not_started",
        }

        catalogue_readiness.reset()
        assert "catalogue" not in health.hangar_health()


# ----------------------------------------------------------------------------
# The retry
# ----------------------------------------------------------------------------


class _CommandBus:
    """Records each start; projects the server, or raises what *fails* maps it to."""

    def __init__(self, fails: dict[str, Exception] | None = None) -> None:
        self.sent: list[StartMcpServerCommand] = []
        self.fails = fails or {}

    def send(self, command: StartMcpServerCommand) -> None:
        self.sent.append(command)
        failure = self.fails.get(command.mcp_server_id)
        if failure is not None:
            raise failure
        _project(command.mcp_server_id)

    def starts(self, server_id: str) -> int:
        return sum(1 for command in self.sent if command.mcp_server_id == server_id)


def _front_door(*requirements: Requirement, retry_for_s: float = 600.0) -> None:
    get_tool_access_resolver().set_topology_mode("front_door")
    configure_required_catalogue(_required(*requirements, retry_for_s=retry_for_s))


def _runtime(bus: _CommandBus, **servers: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(repository=_Repository(**servers), command_bus=bus)


def _wait(condition: Callable[[], bool], deadline_s: float = 5.0) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class _Running:
    """`CatalogueRetry.run` on a thread of its own, stopped and joined on exit."""

    def __init__(self, retry: CatalogueRetry) -> None:
        self.retry = retry
        self.thread = threading.Thread(target=retry.run, name="catalogue-retry-under-test")

    def __enter__(self) -> _Running:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.retry.stop()
        self.thread.join(5.0)
        assert not self.thread.is_alive()


def _retry_state() -> str:
    return catalogue_readiness._gate._retry


class TestTheRetry:
    def test_it_starts_a_server_as_a_call_would_and_ends_once_the_list_is_projected(self) -> None:
        _front_door(_server_req("payments"))
        bus = _CommandBus()

        with _Running(CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST)) as running:
            running.thread.join(5.0)

        # Not deliberate: a dead server waits out its backoff and a capability
        # block is refused -- the two things #1429's reconciler skipped.
        assert [(c.mcp_server_id, c.deliberate) for c in bus.sent] == [("payments", False)]
        assert _retry_state() == "finished"

    @pytest.mark.parametrize("reason", ["given_up", "capability_blocked"])
    def test_it_never_starts_a_server_dead_for(self, reason: str) -> None:
        _front_door(_server_req("payments"), _server_req("search"))
        bus = _CommandBus(fails={"search": RuntimeError("down")})
        runtime = _runtime(bus, payments=_server("dead", reason), search=_server("dead", "start_failed"))

        with _Running(CatalogueRetry(runtime, **FAST)):
            # The retry is alive and working: it keeps trying `search`.
            assert _wait(lambda: bus.starts("search") >= 3)
            assert _retry_state() == "running"

        assert bus.starts("payments") == 0

    def test_it_ends_early_when_nothing_is_left_that_it_may_start(self) -> None:
        _front_door(_server_req("payments"), _server_req("search"))
        bus = _CommandBus()
        runtime = _runtime(bus, payments=_server("dead", "given_up"), search=_server("dead", "capability_blocked"))

        with capture_logs() as logs, _Running(CatalogueRetry(runtime, **FAST)) as running:
            running.thread.join(5.0)
            assert not running.thread.is_alive(), "still running with nothing it may start"

        assert bus.sent == []
        assert _retry_state() == "blocked"
        [entry] = [entry for entry in logs if entry["event"] == "required_catalogue_retry_blocked"]
        assert entry["not_retried"] == {"payments": "given_up", "search": "capability_blocked"}
        body, status = build_readiness_report(runtime.repository)
        assert (status, body["catalogue"]["retry"], body["catalogue"]["holds_readiness"]) == (503, "blocked", True)

    def test_after_an_early_end_readiness_holds_only_until_the_window_ends(self) -> None:
        _front_door(_server_req("payments"), retry_for_s=SHORT_WINDOW_S)
        runtime = _runtime(_CommandBus(), payments=_server("dead", "given_up"))
        CatalogueRetry(runtime, **FAST).run()
        assert (_retry_state(), build_readiness_report(runtime.repository)[1]) == ("blocked", 503)

        _outlive_the_window()

        assert build_readiness_report(runtime.repository)[1] == 200

    def test_it_leaves_a_degraded_server_to_the_recovery_saga(self) -> None:
        _front_door(_server_req("payments"), _server_req("search"))
        bus = _CommandBus(fails={"search": RuntimeError("down")})
        runtime = _runtime(bus, payments=_server("degraded"), search=_server("cold"))

        with _Running(CatalogueRetry(runtime, **FAST)):
            assert _wait(lambda: bus.starts("search") >= 3)

        assert bus.starts("payments") == 0

    def test_it_waits_out_a_dead_servers_backoff(self) -> None:
        _front_door(_server_req("payments"))
        bus = _CommandBus()
        backoff = {"over": False}
        server = _server("dead", "start_failed")
        server.health = SimpleNamespace(can_retry=lambda: backoff["over"])

        with _Running(CatalogueRetry(_runtime(bus, payments=server), **FAST)) as running:
            time.sleep(0.2)
            assert bus.sent == [], "started inside the server's own backoff"
            backoff["over"] = True
            running.thread.join(5.0)

        assert bus.starts("payments") == 1

    def test_it_never_restarts_a_server_that_was_projected(self) -> None:
        # `payments` was projected at boot, then stopped for being idle: cold.
        # A retry still running for `search` must leave it cold.
        _front_door(_server_req("payments"), _server_req("search"))
        _project("payments")
        bus = _CommandBus(fails={"search": RuntimeError("down")})
        runtime = _runtime(bus, payments=_server("cold"), search=_server("cold"))

        with _Running(CatalogueRetry(runtime, **FAST)):
            assert _wait(lambda: bus.starts("search") >= 3)

        assert bus.starts("payments") == 0

    def test_it_stops_for_a_group_once_one_member_is_projected(self) -> None:
        _front_door(Requirement(name="pool", servers=("pool-a", "pool-b")))
        bus = _CommandBus(fails={"pool-a": RuntimeError("down")})
        runtime = _runtime(bus, **{"pool-a": _server("cold"), "pool-b": _server("cold")})

        with _Running(CatalogueRetry(runtime, **FAST)) as running:
            running.thread.join(5.0)

        assert (bus.starts("pool-a"), bus.starts("pool-b")) == (1, 1)
        assert _retry_state() == "finished"

    def test_a_member_projected_earlier_in_the_same_pass_spares_the_others(self) -> None:
        # What replaced a per-server "was it projected" check, which the pass
        # could never reach: it only offered unprojected servers. The question
        # that can change inside a pass is whether the group has been met.
        _front_door(Requirement(name="pool", servers=("pool-a", "pool-b")))
        bus = _CommandBus()
        runtime = _runtime(bus, **{"pool-a": _server("cold"), "pool-b": _server("cold")})

        with _Running(CatalogueRetry(runtime, **FAST)) as running:
            running.thread.join(5.0)

        assert (bus.starts("pool-a"), bus.starts("pool-b")) == (1, 0)

    def test_it_ends_when_the_window_does_and_readiness_falls_back(self) -> None:
        _front_door(_server_req("payments"), retry_for_s=SHORT_WINDOW_S)
        bus = _CommandBus(fails={"payments": RuntimeError("down")})
        runtime = _runtime(bus, payments=_server("cold"))

        with capture_logs() as logs, _Running(CatalogueRetry(runtime, **FAST)) as running:
            running.thread.join(5.0)
            assert not running.thread.is_alive()

        assert _retry_state() == "exhausted"
        assert any(entry["event"] == "required_catalogue_retry_exhausted" for entry in logs)
        body, status = build_readiness_report(runtime.repository)
        assert (status, body["catalogue"]["holds_readiness"], body["catalogue"]["complete"]) == (200, False, False)

    def test_it_does_nothing_in_egress(self) -> None:
        configure_required_catalogue(_required(_server_req("payments")))  # as if applied
        get_tool_access_resolver().set_topology_mode("egress")
        bus = _CommandBus()

        CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST).run()

        assert bus.sent == []

    def test_it_does_nothing_without_a_list(self) -> None:
        get_tool_access_resolver().set_topology_mode("front_door")
        bus = _CommandBus()

        CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST).run()

        assert bus.sent == []

    def test_stop_ends_it_and_nothing_starts_after(self) -> None:
        _front_door(_server_req("payments"))
        bus = _CommandBus(fails={"payments": RuntimeError("down")})

        with _Running(CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST)):
            assert _wait(lambda: bus.starts("payments") >= 2)
        stopped_at = bus.starts("payments")
        time.sleep(0.1)

        assert bus.starts("payments") == stopped_at
        assert _retry_state() == "stopped"

    def test_a_reload_that_removes_the_list_ends_it(self) -> None:
        _front_door(_server_req("payments"))
        bus = _CommandBus(fails={"payments": RuntimeError("down")})

        with _Running(CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST)) as running:
            assert _wait(lambda: bus.starts("payments") >= 1)
            configure_required_catalogue(None)
            running.thread.join(5.0)
            assert not running.thread.is_alive()

        assert _retry_state() == "stopped"

    def test_a_failing_pass_is_logged_by_type_and_the_retry_goes_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _front_door(_server_req("payments"))
        bus = _CommandBus()
        failures = {"left": 3}
        observe = catalogue_readiness._gate.observe

        def flaky(repository: Any) -> Any:
            if failures["left"]:
                failures["left"] -= 1
                raise RuntimeError("repository said: token=sk-live-do-not-log")
            return observe(repository)

        monkeypatch.setattr(catalogue_readiness._gate, "observe", flaky)

        with (
            capture_logs() as logs,
            _Running(CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST)) as running,
        ):
            running.thread.join(5.0)

        errors = [entry for entry in logs if entry["event"] == "required_catalogue_retry_error"]
        assert [entry["error_type"] for entry in errors] == ["RuntimeError"] * 3
        assert "sk-live" not in repr(logs)
        assert (bus.starts("payments"), _retry_state()) == (1, "finished")

    def test_anything_that_escapes_still_ends_in_a_final_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _front_door(_server_req("payments"))
        retry = CatalogueRetry(_runtime(_CommandBus(), payments=_server("cold")), **FAST)

        def escape() -> str:
            raise RuntimeError("down")

        monkeypatch.setattr(retry, "_loop", escape)

        with capture_logs() as logs:
            retry.run()

        assert _retry_state() == "failed"
        assert [entry["error_type"] for entry in logs if entry["event"] == "required_catalogue_retry_failed"] == [
            "RuntimeError"
        ]

    def test_each_attempt_logs_the_error_type_and_never_the_error_text(self) -> None:
        _front_door(_server_req("payments"), _server_req("search"))
        bus = _CommandBus(
            fails={
                "payments": RuntimeError("upstream said: token=sk-live-do-not-log"),
                "search": CannotStartMcpServerError("search", "backoff not elapsed, retry in 1.0s", 1.0),
            }
        )
        runtime = _runtime(bus, payments=_server("cold"), search=_server("cold"))

        with capture_logs() as logs, _Running(CatalogueRetry(runtime, **FAST)):
            assert _wait(lambda: bus.starts("payments") >= 1 and bus.starts("search") >= 1)

        attempts = [entry for entry in logs if entry["event"] == "required_catalogue_retry"]
        by_server = {entry["mcp_server_id"]: entry for entry in attempts}
        assert (by_server["payments"]["outcome"], by_server["payments"]["error_type"]) == ("failed", "RuntimeError")
        assert (by_server["search"]["outcome"], by_server["search"]["error_type"]) == (
            "refused",
            "CannotStartMcpServerError",
        )
        assert "sk-live" not in repr(logs)
        assert len(attempts) == bus.starts("payments") + bus.starts("search"), "one log line per attempt"
        assert 'mcp_hangar_catalogue_retries_total{mcp_server="payments",outcome="failed"}' in get_metrics()


# ----------------------------------------------------------------------------
# The start it sends, a call's cold start, and shutdown
# ----------------------------------------------------------------------------


class TestTheStartCommand:
    @pytest.mark.parametrize(("deliberate", "by_call"), [(True, False), (False, True)])
    def test_a_start_that_is_not_deliberate_is_a_calls_start(self, deliberate: bool, by_call: bool) -> None:
        server = MagicMock()
        server.state.value = "ready"
        server.get_tool_names.return_value = []
        server.collect_events.return_value = []
        repository = MagicMock()
        repository.get.return_value = server
        handler = StartMcpServerHandler(repository, MagicMock(), None)

        handler.handle(StartMcpServerCommand(mcp_server_id="payments", deliberate=deliberate))

        server.ensure_ready.assert_called_once_with(by_call=by_call)

    def test_operator_and_saga_starts_are_deliberate_by_default(self) -> None:
        assert StartMcpServerCommand(mcp_server_id="payments").deliberate is True


class TestACallsColdStartFollowsTheCallRules:
    def test_the_executor_sends_a_start_that_is_not_deliberate(self) -> None:
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        sent: list[StartMcpServerCommand] = []
        pipeline = _pipeline(SimpleNamespace(command_bus=SimpleNamespace(send=sent.append)), "cold")

        assert BatchExecutor()._gate_cold_start(pipeline) is None

        assert [(command.mcp_server_id, command.deliberate) for command in sent] == [("svc", False)]

    def test_a_server_blocked_after_the_executor_checked_it_is_not_revived(self) -> None:
        # The executor read `cold` and let the call through; the server was then
        # blocked for a capability drift. A deliberate start revived it anyway.
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        fleet = _Fleet()
        with fleet.server._lock:
            fleet.server._mark_dead(DEAD_CAPABILITY_BLOCKED)
        pipeline = _pipeline(SimpleNamespace(command_bus=fleet.bus), "cold")  # the read that passed

        refusal = BatchExecutor()._gate_cold_start(pipeline)

        # The code the executor's own check gives a capability block, not a failed start.
        assert refusal is not None and refusal.error_type == "CannotStartMcpServerError"
        assert (fleet.server.state.value, fleet.server.dead_reason_snapshot) == ("dead", DEAD_CAPABILITY_BLOCKED)
        assert fleet.upstreams == [], "the blocked server was launched"


class TestARefusedColdStartIsCodedAsTheExecutorsOwnRefusal:
    def test_a_server_back_in_its_backoff_is_refused_as_the_circuit_breaker_refuses_it(self) -> None:
        # The executor's check drew its jitter and passed; the server's own
        # draw said no. The call used to fail as `McpServerStartError`.
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        fleet = _Fleet()
        launches: list[str] = []

        def broken() -> Any:
            launches.append("launch")
            raise OSError("connection refused")

        fleet.server._create_client = broken  # type: ignore[method-assign]
        with pytest.raises(Exception):  # noqa: B017 -- any start failure: what matters is the backoff it leaves
            fleet.bus.send(StartMcpServerCommand(mcp_server_id=fleet.sid))
        pipeline = _pipeline(SimpleNamespace(command_bus=fleet.bus), "cold")  # the read that passed

        refusal = BatchExecutor()._gate_cold_start(pipeline)

        assert refusal is not None and refusal.error_type == "CircuitBreakerOpen", refusal
        assert "retry in" in (refusal.error or "")
        assert launches == ["launch"], "started inside its backoff"

    def test_it_counts_against_a_group_member_as_the_executors_own_refusal_does(self) -> None:
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        fleet = _Fleet()
        with fleet.server._lock:
            fleet.server._mark_dead(DEAD_CAPABILITY_BLOCKED)
        # `event_bus`: reporting the refusal to the group drains it (#1410).
        pipeline = _pipeline(SimpleNamespace(command_bus=fleet.bus, event_bus=MagicMock()), "cold")
        pipeline.is_group = True
        pipeline.group_obj = MagicMock()

        BatchExecutor()._gate_cold_start(pipeline)

        pipeline.group_obj.report_failure.assert_called_once_with("svc")


@pytest.mark.parametrize("backend", ["SQLite", " sqlite ", "", None])
def test_a_backend_the_loader_reads_as_local_or_absent_takes_no_lease(backend: str | None) -> None:
    # As `bootstrap.persistence.select_backend` reads it: case-blind, and empty is none.
    assert required_catalogue({**_config("payments"), "persistence": {"backend": backend}}) is not None


@pytest.mark.parametrize("backend", ["PostgreSQL", " postgresql "])
def test_a_shared_backend_is_recognised_in_any_spelling(backend: str) -> None:
    with pytest.raises(ConfigurationError, match="shared `postgresql` backend"):
        required_catalogue({**_config("payments"), "persistence": {"backend": backend}})


class TestAWindowShorterThanBootAndTheWarmUp:
    def test_each_missing_server_still_gets_one_attempt(self) -> None:
        _front_door(_server_req("payments"), _server_req("search"), retry_for_s=SHORT_WINDOW_S)
        _outlive_the_window()  # boot and the warm-up took longer than the window
        bus = _CommandBus(fails={"search": RuntimeError("down")})
        runtime = _runtime(bus, payments=_server("cold"), search=_server("dead", "start_failed"))

        with _Running(CatalogueRetry(runtime, **FAST)) as running:
            running.thread.join(5.0)
            assert not running.thread.is_alive()

        assert (bus.starts("payments"), bus.starts("search")) == (1, 1), "one attempt each, and no more"
        assert _retry_state() == "exhausted"
        assert build_readiness_report(runtime.repository)[1] == 200, "the window bounds readiness all the same"

    def test_that_attempt_still_waits_out_the_backoff(self) -> None:
        _front_door(_server_req("payments"), retry_for_s=SHORT_WINDOW_S)
        _outlive_the_window()
        bus = _CommandBus()
        backoff = {"over": False}
        server = _server("dead", "start_failed")
        server.health = SimpleNamespace(can_retry=lambda: backoff["over"])
        runtime = _runtime(bus, payments=server)

        with _Running(CatalogueRetry(runtime, **FAST)) as running:
            time.sleep(0.2)
            assert bus.sent == [] and running.thread.is_alive()
            body, status = build_readiness_report(runtime.repository)
            assert (status, body["catalogue"]["retry"]) == (200, "running"), "owed an attempt, and not held"
            backoff["over"] = True
            running.thread.join(5.0)

        assert (bus.starts("payments"), _retry_state()) == (1, "finished")

    def test_servers_it_may_not_start_are_owed_nothing(self) -> None:
        _front_door(_server_req("payments"), retry_for_s=SHORT_WINDOW_S)
        _outlive_the_window()
        bus = _CommandBus()

        CatalogueRetry(_runtime(bus, payments=_server("dead", "given_up")), **FAST).run()

        assert (bus.sent, _retry_state()) == ([], "exhausted")


def test_a_readiness_check_that_raises_falls_back_to_todays_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_required_catalogue(_required(_server_req("payments")))

    def boom(repository: Any) -> Any:
        raise RuntimeError("repository said: token=sk-live-do-not-log")

    monkeypatch.setattr(catalogue_readiness._gate, "observe", boom)

    with capture_logs() as logs:
        answers = [build_readiness_report(_Repository(payments=_server("cold"))) for _ in range(3)]

    for body, status in answers:
        assert status == 200
        assert "catalogue" not in body
    failures = [entry for entry in logs if entry["event"] == "required_catalogue_readiness_failed"]
    assert [entry["error_type"] for entry in failures] == ["RuntimeError"], "logged once, by type"
    assert "sk-live" not in repr(logs)


def test_shutdown_stops_the_retry() -> None:
    _front_door(_server_req("payments"))
    bus = _CommandBus(fails={"payments": RuntimeError("down")})
    lifecycle = ServerLifecycle(ApplicationContext(runtime=MagicMock(), mcp_server=MagicMock()))
    lifecycle._catalogue_retry = CatalogueRetry(_runtime(bus, payments=_server("cold")), **FAST)
    thread = threading.Thread(target=lifecycle._catalogue_retry.run, name="catalogue-retry-under-test")
    thread.start()
    assert _wait(lambda: bus.starts("payments") >= 2)

    lifecycle.shutdown()

    assert not thread.is_alive(), "shutdown returned with the retry still running"
    stopped_at = bus.starts("payments")
    time.sleep(0.1)
    assert bus.starts("payments") == stopped_at
    assert _retry_state() == "stopped"
