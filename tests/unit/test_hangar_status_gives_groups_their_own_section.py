"""`hangar_status` gives groups their own section, and its frame closes (#1378).

Observed on a live gateway: a group rendered as `[?]` in the server table, every
row overran the right border, and `weather-open-meteo` was cut to
`weather-open-me`, which is not a valid id and cannot be pasted anywhere.

The decision recorded on the issue: server states are a lifecycle (cold,
initializing, ready, degraded, dead) and a group's state is availability
computed from its members (inactive, partial, healthy, degraded). They are two
vocabularies and they stay two. So the tests pin that:

- a group renders in its own section, with its own columns, and never in the
  server indicator column;
- every state of either vocabulary has an indicator. The states come from the
  enums, so a state added without an indicator fails here;
- every line of the frame has the same display width, whatever the names;
- `hangar_list` and `hangar_details` still return each vocabulary as it is.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Iterator
from typing import Any

import pytest

from mcp_hangar.application.queries import register_all_handlers
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects import GroupState, McpServerState
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.context import get_context, init_context, reset_context
from mcp_hangar.server.tools import mcp_server as mcp_server_tools
from mcp_hangar.server.tools.hangar import (
    _format_status_dashboard,
    _get_status_indicator,
    _group_indicator,
    hangar_list,
    hangar_status,
)

#: The indicators the `hangar_status` docstring documents, one set per vocabulary.
SERVER_INDICATORS = {"[READY]", "[COLD]", "[STARTING]", "[DEGRADED]", "[DEAD]"}
GROUP_INDICATORS = {"[HEALTHY]", "[PARTIAL]", "[INACTIVE]", "[DEGRADED]"}

#: As long as `McpServerId` and `GroupId` allow, longer than any column is sized.
LONG_SERVER_ID = ("a-server-name-far-longer-than-a-column-" * 2)[:64]
LONG_GROUP_ID = ("a-group-name-far-longer-than-a-column-" * 2)[:64]

#: What `McpServerGroup.to_status_dict()` returns, which both data tools pass through.
GROUP_STATUS_KEYS = {
    "group_id",
    "description",
    "state",
    "strategy",
    "min_healthy",
    "healthy_count",
    "members_in_rotation_count",
    "total_members",
    "is_available",
    "circuit_open",
    "members",
}


@pytest.fixture(autouse=True)
def fresh_context() -> Iterator[None]:
    reset_context()
    yield
    reset_context()


def _replica(states: dict[str, str]) -> InMemoryMcpServerRepository:
    """A context whose repository holds these servers, each forced into its state."""
    repository = InMemoryMcpServerRepository()
    for mcp_server_id, state in states.items():
        server = McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["true"])
        server._state = McpServerState(state)
        repository.add(mcp_server_id, server)
    query_bus = QueryBus()
    init_context(create_runtime(repository=repository, query_bus=query_bus))
    register_all_handlers(query_bus, repository, event_store=InMemoryEventStore())
    return repository


def _group(
    repository: InMemoryMcpServerRepository, group_id: str, member_ids: Iterable[str], **options: Any
) -> McpServerGroup:
    group = McpServerGroup(group_id=group_id, auto_start=False, **options)
    for member_id in member_ids:
        member = repository.get(member_id)
        assert member is not None
        group.add_member(member)
    get_context().groups[group_id] = group
    return group


def _drive_healthy(group: McpServerGroup, member_ids: Iterable[str]) -> None:
    """Put ready members into rotation the way traffic does: by succeeding."""
    for member_id in member_ids:
        for _ in range(5):
            group.report_success(member_id)


def _frame(formatted: str) -> list[str]:
    lines = formatted.splitlines()
    top = next(n for n, line in enumerate(lines) if line.startswith("╭"))
    bottom = next(n for n, line in enumerate(lines) if line.startswith("╰"))
    return lines[top : bottom + 1]


def _sections(formatted: str) -> list[list[str]]:
    """The frame's content rows, split at its rules, with the borders stripped."""
    sections: list[list[str]] = [[]]
    for line in _frame(formatted)[1:-1]:
        if line.startswith("├"):
            sections.append([])
        else:
            sections[-1].append(line[2:-2].rstrip())
    return sections


def _display_width(text: str) -> int:
    """Terminal columns, measured independently of the renderer's own helper."""
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text
    )


def _assert_the_frame_closes(formatted: str) -> None:
    frame = _frame(formatted)
    assert {_display_width(line) for line in frame} == {_display_width(frame[0])}, "\n".join(frame)
    assert frame[0][0] + frame[0][-1] == "╭╮"
    assert frame[-1][0] + frame[-1][-1] == "╰╯"
    for line in frame[1:-1]:
        assert line[0] + line[-1] in ("││", "├┤"), line


class TestAGroupHasItsOwnSection:
    """The observed case: a healthy group beside servers, which rendered as `[?]`."""

    @pytest.fixture
    def status(self) -> dict:
        repository = _replica({"github": "cold", "weather-nws": "ready", "weather-open-meteo": "ready"})
        group = _group(repository, "weather", ["weather-nws", "weather-open-meteo"])
        _drive_healthy(group, ["weather-nws", "weather-open-meteo"])
        return hangar_status()

    def test_the_group_really_is_healthy(self, status: dict) -> None:
        """Guard: `healthy` is the state that fell through to `[?]`."""
        assert status["groups"][0]["state"] == "healthy"

    def test_nothing_renders_as_unknown(self, status: dict) -> None:
        assert "[?]" not in status["formatted"]
        indicators = [entry["indicator"] for key in ("mcp_servers", "groups") for entry in status[key]]
        assert "[?]" not in indicators

    def test_servers_and_groups_are_separate_sections_with_their_own_columns(self, status: dict) -> None:
        title, servers, groups, footer = _sections(status["formatted"])

        assert servers[0].split() == ["STATUS", "SERVER", "STATE", "NOTE"]
        assert groups[0].split() == ["GROUP", "STATE", "HEALTHY", "CIRCUIT"]
        assert groups[1].split() == ["weather", "healthy", "2/2", "closed"]
        assert [row.split()[1] for row in servers[1:]] == ["github", "weather-nws", "weather-open-meteo"]

    def test_a_group_row_carries_no_server_indicator(self, status: dict) -> None:
        groups = _sections(status["formatted"])[2]

        assert not [row for row in groups for indicator in SERVER_INDICATORS if indicator in row]

    def test_the_structured_group_entry_speaks_the_group_vocabulary(self, status: dict) -> None:
        assert status["groups"] == [
            {
                "id": "weather",
                "indicator": "[HEALTHY]",
                "state": "healthy",
                "healthy_members": 2,
                "members_in_rotation_count": 2,
                "total_members": 2,
                "circuit_open": False,
            }
        ]

    def test_an_open_circuit_is_shown_as_open(self) -> None:
        repository = _replica({"alpha": "ready", "beta": "ready"})
        group = _group(repository, "pool", ["alpha", "beta"], circuit_failure_threshold=1)
        _drive_healthy(group, ["alpha", "beta"])
        group.report_failure("alpha")

        status = hangar_status()

        assert status["groups"][0]["circuit_open"] is True
        row = _sections(status["formatted"])[2][1].split()
        assert (row[0], row[1], row[-1]) == ("pool", "degraded", "open")


class TestEveryStateHasAnIndicator:
    """Enumerated from the enums: a state added without an indicator fails here."""

    @pytest.mark.parametrize("state", list(McpServerState), ids=str)
    def test_every_server_state_has_a_documented_indicator(self, state: McpServerState) -> None:
        assert _get_status_indicator(state.value) in SERVER_INDICATORS

    def test_dead_reads_dead(self) -> None:
        """`dead` is the newest server state (#1399), and it has its own indicator."""
        assert _get_status_indicator("dead") == "[DEAD]"

    @pytest.mark.parametrize("state", list(GroupState), ids=str)
    def test_every_group_state_has_a_documented_indicator(self, state: GroupState) -> None:
        assert _group_indicator(state.value) in GROUP_INDICATORS

    @pytest.mark.parametrize("state", list(McpServerState), ids=str)
    def test_every_server_state_renders_in_the_dashboard(self, state: McpServerState) -> None:
        _replica({"probe": state.value})

        status = hangar_status()

        assert "[?]" not in status["formatted"]
        row = _sections(status["formatted"])[1][1].split()
        assert row[:3] == [_get_status_indicator(state.value), "probe", state.value]

    @pytest.mark.parametrize("state", list(GroupState), ids=str)
    def test_every_group_state_renders_in_the_dashboard(self, state: GroupState) -> None:
        repository = _replica({"member": "ready"})
        group = _group(repository, "pool", ["member"])
        group._state = state

        status = hangar_status()

        assert "[?]" not in status["formatted"]
        assert _sections(status["formatted"])[2][1].split()[:2] == ["pool", state.value]

    def test_only_a_string_that_is_no_state_is_unknown(self) -> None:
        """The fallback is for a string outside both vocabularies, never for a state."""
        assert _get_status_indicator("unknown") == "[?]"
        assert _group_indicator("unknown") == "[?]"


class TestTheFrameCloses:
    """Every line from the top border to the bottom one has the same display width."""

    @pytest.fixture
    def status(self) -> dict:
        """Every server state side by side, `[READY]` rows beside `[COLD]` ones, and long names."""
        states = {f"server-{state.value}": state.value for state in McpServerState}
        states |= {"weather-open-meteo": "cold", LONG_SERVER_ID: "ready"}
        repository = _replica(states)
        _group(repository, LONG_GROUP_ID, ["weather-open-meteo", LONG_SERVER_ID])
        return hangar_status()

    def test_the_frame_closes_with_every_state_and_long_names(self, status: dict) -> None:
        _assert_the_frame_closes(status["formatted"])

    def test_a_name_that_fits_is_never_cut(self, status: dict) -> None:
        """The live report cut `weather-open-meteo` to `weather-open-me`."""
        servers = _sections(status["formatted"])[1]

        assert "weather-open-meteo" in [row.split()[1] for row in servers]

    @pytest.mark.parametrize(
        ("section", "id_column", "full_id"), [(1, 1, LONG_SERVER_ID), (2, 0, LONG_GROUP_ID)], ids=["server", "group"]
    )
    def test_a_name_that_does_not_fit_is_visibly_elided(
        self, status: dict, section: int, id_column: int, full_id: str
    ) -> None:
        rows = _sections(status["formatted"])[section][1:]
        cells = [row.split()[id_column] for row in rows if row.split()[id_column].endswith("…")]

        assert len(cells) == 1
        assert full_id.startswith(cells[0][:-1])
        # The full id is still in the structured answer, for anything that needs to use it.
        assert full_id in [entry["id"] for key in ("mcp_servers", "groups") for entry in status[key]]

    def test_columns_align_across_indicators_of_different_lengths(self, status: dict) -> None:
        """`[READY]` rows sat one character right of `[COLD]` rows."""
        rows = _sections(status["formatted"])[1][1:]

        assert len({row.index(row.split()[1]) for row in rows}) == 1

    @pytest.mark.parametrize(
        ("servers", "groups"),
        [
            pytest.param(
                [{"id": "s" * 200, "indicator": "[READY]", "state": "x" * 200, "note": "n" * 200}],
                [{"id": "g" * 200, "state": "y" * 200, "healthy_members": 1, "total_members": 1, "circuit_open": True}],
                id="every-cell-too-long",
            ),
            pytest.param(
                [{"id": "天気サーバー" * 12, "indicator": "[COLD]", "state": "cold", "note": "é" * 60}],
                [
                    {
                        "id": "組" * 50,
                        "state": "partial",
                        "healthy_members": 1,
                        "total_members": 3,
                        "circuit_open": False,
                    }
                ],
                id="wide-and-combining-characters",
            ),
            pytest.param([], [], id="nothing-configured"),
        ],
    )
    def test_the_frame_closes_whatever_it_holds(self, servers: list, groups: list) -> None:
        formatted = _format_status_dashboard(servers, groups, 0, len(servers), "12h 3m", "hangar-0-3fa81c2e")

        _assert_the_frame_closes(formatted)


class TestTheDataToolsKeepTheirVocabularies:
    """The decision: only the renderer changes. The data says what each thing is."""

    @pytest.fixture
    def group(self) -> McpServerGroup:
        repository = _replica({"alpha": "ready", "beta": "ready", "gamma": "cold", "delta": "dead"})
        group = _group(repository, "pool", ["alpha", "beta"])
        _drive_healthy(group, ["alpha", "beta"])
        assert group.state is GroupState.HEALTHY
        return group

    @pytest.fixture
    def hangar_details(self) -> Any:
        registered: dict[str, Any] = {}

        class _Mcp:
            def tool(self, name: str | None = None, **_kwargs: Any) -> Any:
                def register(fn: Any) -> Any:
                    registered[name or fn.__name__] = fn
                    return fn

                return register

        mcp_server_tools.register_mcp_server_tools(_Mcp())
        return registered["hangar_details"]

    def test_hangar_list_returns_the_group_as_the_group_reports_it(self, group: McpServerGroup) -> None:
        (entry,) = hangar_list()["groups"]

        assert entry == group.to_status_dict()
        assert set(entry) == GROUP_STATUS_KEYS
        assert entry["state"] == "healthy"

    def test_hangar_list_returns_server_states_from_the_lifecycle(self, group: McpServerGroup) -> None:
        servers = {s["mcp_server_id"]: s["state"] for s in hangar_list()["mcp_servers"]}

        assert servers == {"alpha": "ready", "beta": "ready", "gamma": "cold", "delta": "dead"}

    def test_hangar_list_filters_each_vocabulary_as_it_is(self, group: McpServerGroup) -> None:
        healthy = hangar_list(state_filter="healthy")
        dead = hangar_list(state_filter="dead")

        assert ([g["group_id"] for g in healthy["groups"]], healthy["mcp_servers"]) == (["pool"], [])
        assert ([s["mcp_server_id"] for s in dead["mcp_servers"]], dead["groups"]) == (["delta"], [])

    def test_hangar_details_returns_the_group_as_the_group_reports_it(
        self, group: McpServerGroup, hangar_details: Any
    ) -> None:
        details = hangar_details("pool")

        assert details == group.to_status_dict()
        assert details["state"] == "healthy"

    def test_hangar_details_returns_a_server_state_from_the_lifecycle(
        self, group: McpServerGroup, hangar_details: Any
    ) -> None:
        assert hangar_details("delta")["state"] == "dead"
        assert hangar_details("alpha")["state"] == "ready"
