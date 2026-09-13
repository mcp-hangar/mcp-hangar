"""`hangar_status` says which replica answered, and `hangar_health` agrees with it (#1380).

Observed on a live gateway: two `hangar_status` calls a few minutes apart. One
reported uptime 34.8s and 8/8 healthy, the other 4819.6s and 2/8. Nothing about
the fleet had changed. Under session affinity the two calls reached two
replicas, and each answered with its own local view, in a shape that read as a
fleet fact.

The decision recorded on the issue is "honest replica-local": no aggregation and
no shared state. Each response names the replica that answered and says it
describes that replica only. So the tests pin two things:

- two replicas with divergent state produce answers that each carry their own
  replica's name, uptime and scope, so neither can be read as a claim about the
  fleet;
- `hangar_health` and `hangar_status` read one snapshot, so from the same
  replica they report the same servers and groups. Before this they read
  separately, and `hangar_health` did not count hot-loaded servers.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
import time

import pytest

from mcp_hangar.application.queries import register_all_handlers
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.domain.events.producer import current_instance_id, set_instance_id
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.infrastructure.runtime_store import LoadMetadata
from mcp_hangar.server.context import get_context, init_context, reset_context
from mcp_hangar.server.state import get_runtime_mcp_servers
from mcp_hangar.server.tools import replica_view
from mcp_hangar.server.tools.hangar import hangar_status
from mcp_hangar.server.tools.health import hangar_health

_HOT_LOADED_ID = "hot-loaded-probe"


@pytest.fixture(autouse=True)
def isolated_replica() -> Iterator[None]:
    """Instance identity, context and the runtime store are process-wide; restore all three."""
    import mcp_hangar.domain.events.producer as producer

    identity_before = producer._instance_id
    reset_context()
    yield
    get_runtime_mcp_servers().remove(_HOT_LOADED_ID)
    reset_context()
    producer._instance_id = identity_before


def _server(mcp_server_id: str, state: str) -> McpServer:
    server = McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["true"])
    server._state = McpServerState(state)
    return server


def _become_replica(
    monkeypatch: pytest.MonkeyPatch, label: str, states: dict[str, str], uptime_s: float
) -> InMemoryMcpServerRepository:
    """Make this process look like one replica: its own identity, uptime and local servers."""
    reset_context()
    repository = InMemoryMcpServerRepository()
    for mcp_server_id, state in states.items():
        repository.add(mcp_server_id, _server(mcp_server_id, state))
    query_bus = QueryBus()
    init_context(create_runtime(repository=repository, query_bus=query_bus))
    register_all_handlers(query_bus, repository, event_store=InMemoryEventStore())
    set_instance_id(label)
    monkeypatch.setattr(replica_view, "_PROCESS_STARTED_AT", time.time() - uptime_s)
    return repository


class TestTwoReplicasCannotBeReadAsOneFleet:
    """The observed pair: 34.8s and 8/8 ready, then 4819.6s and 2/8 ready."""

    @pytest.fixture
    def answers(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
        fleet = [f"server-{n}" for n in range(8)]

        _become_replica(monkeypatch, "gateway-a", dict.fromkeys(fleet, "ready"), uptime_s=34.8)
        a = {"status": hangar_status(), "health": hangar_health(), "instance_id": current_instance_id()}

        states_b = {sid: ("ready" if n < 2 else "cold") for n, sid in enumerate(fleet)}
        _become_replica(monkeypatch, "gateway-b", states_b, uptime_s=4819.6)
        b = {"status": hangar_status(), "health": hangar_health(), "instance_id": current_instance_id()}

        return {"a": a, "b": b}

    def test_the_state_really_diverges(self, answers: dict[str, dict]) -> None:
        """Guard: without divergent state the rest of this class would prove nothing."""
        assert answers["a"]["status"]["summary"]["healthy_mcp_servers"] == 8
        assert answers["b"]["status"]["summary"]["healthy_mcp_servers"] == 2

    @pytest.mark.parametrize("tool", ["status", "health"])
    def test_each_answer_is_scoped_to_a_replica(self, answers: dict[str, dict], tool: str) -> None:
        for replica in ("a", "b"):
            answer = answers[replica][tool]
            assert answer["scope"] == "replica"
            assert "not the fleet" in answer["scope_note"]

    @pytest.mark.parametrize("tool", ["status", "health"])
    def test_each_answer_names_the_replica_that_answered(self, answers: dict[str, dict], tool: str) -> None:
        a, b = answers["a"], answers["b"]

        assert a[tool]["replica"]["instance_id"] == a["instance_id"]
        assert b[tool]["replica"]["instance_id"] == b["instance_id"]
        assert a[tool]["replica"]["instance_id"] != b[tool]["replica"]["instance_id"]
        # The label an operator recognises survives into the name.
        assert a[tool]["replica"]["instance_id"].startswith("gateway-a-")
        assert b[tool]["replica"]["instance_id"].startswith("gateway-b-")

    def test_uptime_belongs_to_the_replica(self, answers: dict[str, dict]) -> None:
        """Two uptimes a process apart are two replicas, and the answer says whose each is."""
        for replica, expected in (("a", 34.8), ("b", 4819.6)):
            status = answers[replica]["status"]
            assert status["replica"]["uptime_seconds"] == pytest.approx(expected, abs=1.0)
            # The legacy summary field stays, and it is the same replica's number.
            assert status["summary"]["uptime_seconds"] == status["replica"]["uptime_seconds"]
            assert status["summary"]["uptime"] == status["replica"]["uptime"]

    def test_the_dashboard_names_its_replica_before_anything_else(self, answers: dict[str, dict]) -> None:
        for replica in ("a", "b"):
            status = answers[replica]["status"]
            lines = status["formatted"].splitlines()

            assert lines[0] == f"Answered by replica: {status['replica']['instance_id']}"
            assert "not the fleet" in lines[1]
            assert any("MCP-Hangar Status (this replica)" in line for line in lines)
            assert any(line.startswith("│ Replica uptime: ") for line in lines)
            assert not any(line.startswith("│ Uptime:") for line in lines)

    def test_the_changed_rows_keep_the_frame_width(self, answers: dict[str, dict]) -> None:
        """The title and uptime rows changed words, not width; the frame itself is #1378's."""
        lines = answers["b"]["status"]["formatted"].splitlines()
        border = next(line for line in lines if line.startswith("╭"))
        title = next(line for line in lines if "MCP-Hangar Status" in line)
        uptime = next(line for line in lines if line.startswith("│ Replica uptime: "))

        assert len(title) == len(border)
        assert len(uptime) == len(border)


class TestHealthAndStatusAgree:
    """One replica, one snapshot, two tools: same servers, same groups, same replica."""

    @pytest.fixture
    def both(self, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
        repository = _become_replica(
            monkeypatch,
            "gateway-a",
            {"alpha": "ready", "beta": "cold", "gamma": "degraded"},
            uptime_s=120.0,
        )
        # A hot-loaded server lives in the runtime store, not the repository.
        # `hangar_health` used to skip it, which is where the tools disagreed.
        get_runtime_mcp_servers().add(
            _server(_HOT_LOADED_ID, "ready"),
            LoadMetadata(loaded_at=datetime.now(), loaded_by=None, source="registry:probe", verified=True),
        )
        group = McpServerGroup(group_id="pool", auto_start=False)
        for member_id in ("alpha", "beta"):
            member = repository.get(member_id)
            assert member is not None
            group.add_member(member)
        get_context().groups["pool"] = group

        return hangar_status(), hangar_health()

    def test_they_name_the_same_replica(self, both: tuple[dict, dict]) -> None:
        status, health = both

        assert status["replica"]["instance_id"] == health["replica"]["instance_id"] == current_instance_id()
        assert status["scope"] == health["scope"] == "replica"

    def test_they_count_the_same_servers(self, both: tuple[dict, dict]) -> None:
        status, health = both

        assert health["mcp_servers"]["total"] == status["summary"]["total_mcp_servers"] == 4
        assert sum(health["mcp_servers"]["by_state"].values()) == health["mcp_servers"]["total"]

    def test_they_agree_on_how_many_are_ready(self, both: tuple[dict, dict]) -> None:
        status, health = both

        assert health["mcp_servers"]["by_state"]["ready"] == status["summary"]["healthy_mcp_servers"] == 2

    def test_they_agree_on_the_groups(self, both: tuple[dict, dict]) -> None:
        status, health = both

        assert health["groups"]["total"] == len(status["groups"]) == 1
        assert health["groups"]["total_members"] == sum(g["total_members"] for g in status["groups"])
        assert health["groups"]["healthy_members"] == sum(g["healthy_members"] for g in status["groups"])

    def test_hot_loaded_servers_are_counted_by_both(self, both: tuple[dict, dict]) -> None:
        status, health = both

        assert [s["id"] for s in status["runtime_mcp_servers"]] == [_HOT_LOADED_ID]
        assert health["mcp_servers"]["total"] == len(status["mcp_servers"]) + len(status["runtime_mcp_servers"])
