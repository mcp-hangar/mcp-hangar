"""A refusing capability mode stops the start and every call, on the aggregate.

``enforcement_mode: block`` is meant to keep an upstream that serves tools
outside its declared ``expected_tools`` from being used. The check ran in
``_finalize_start`` after the client was kept, READY entered and
``McpServerStarted`` recorded. Block mode then moved the server to DEAD, but
closed nothing and stopped nothing, and ``invoke_tool`` never looked at the state
again. So the call that started the server invoked its tool, undeclared tools
included, on a client left open. Every later call restarted the server and did
the same.

``enforcement_mode: quarantine``, documented to stop a server serving new
requests, did not act on drift at all: it served like ``alert``. Both modes now
refuse alike, and quarantine also records ``McpServerCapabilityQuarantined``.

The start that finds the drift fails, and the server reads DEAD for a
capability block (``DEAD_CAPABILITY_BLOCKED``): no call starts it again, and a
deliberate start checks its tools again.

These tests drive the aggregate with a stand-in transport that records every
request, so "reaches no tool" means the upstream received no ``tools/call``. The
served paths, with a real stdio upstream, are in
tests/integration/test_a_capability_block_stops_the_call_that_found_it.py.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.events import (
    CapabilityViolationDetected,
    McpServerCapabilityQuarantined,
    McpServerDegraded,
    McpServerStarted,
    McpServerStateChanged,
)
from mcp_hangar.domain.exceptions import (
    CannotStartMcpServerError,
    CapabilityBlockedError,
    McpServerNotReadyError,
    McpServerStartError,
    ToolInvocationError,
)
from mcp_hangar.domain.model.mcp_server import DEAD_CAPABILITY_BLOCKED, McpServer
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.capabilities import McpServerCapabilities, ToolCapabilities

DECLARED, UNDECLARED = "add", "exfiltrate"
DRIFTED = (DECLARED, UNDECLARED)
#: The enforcement modes that refuse a server whose tools drifted.
REFUSING = ("block", "quarantine")


class _Upstream:
    """A stand-in transport client that answers the handshake and records every request.

    ``tools_lists`` are its successive ``tools/list`` answers; the last one repeats.
    ``hold``, when given, keeps ``tools/list`` from answering until it is set.
    """

    def __init__(self, tools_lists: list[tuple[str, ...]], hold: threading.Event | None) -> None:
        self._tools_lists = list(tools_lists)
        self._hold = hold
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closes = 0
        self.modern_envelope = False

    def call(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        self.requests.append((method, params))
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-06-18"}}
        if method == "tools/list":
            if self._hold is not None:
                self._hold.wait(timeout=10)
            names = self._tools_lists.pop(0) if len(self._tools_lists) > 1 else self._tools_lists[0]
            return {"result": {"tools": [{"name": name, "inputSchema": {}} for name in names]}}
        if method == "tools/call":
            return {"result": {"content": [{"type": "text", "text": f"{params['name']} ran"}]}}
        return {"result": {}}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        pass

    def is_alive(self) -> bool:
        return self.closes == 0

    def close(self) -> None:
        self.closes += 1

    def methods(self) -> list[str]:
        return [method for method, _ in self.requests]


class _Fleet:
    """One server, and every upstream its starts launched."""

    def __init__(
        self,
        mode: str = "block",
        tools_lists: tuple[tuple[str, ...], ...] = (DRIFTED,),
        hold: threading.Event | None = None,
        max_consecutive_failures: int = 3,
    ) -> None:
        self.publisher = MagicMock()
        self.launched: list[_Upstream] = []
        self._tools_lists = list(tools_lists)
        self._hold = hold
        self.server = McpServer(
            mcp_server_id="drifting",
            mode="subprocess",
            command=["unused"],
            capabilities=McpServerCapabilities(
                tools=ToolCapabilities(expected_tools=(DECLARED,)),
                enforcement_mode=mode,
            ),
            metrics_publisher=self.publisher,
            max_consecutive_failures=max_consecutive_failures,
        )
        self.server._create_client = self._launch  # type: ignore[method-assign]

    def _launch(self) -> _Upstream:
        upstream = _Upstream(self._tools_lists, self._hold)
        self.launched.append(upstream)
        return upstream

    @property
    def tools_called(self) -> list[str]:
        return [
            params["name"]
            for upstream in self.launched
            for method, params in upstream.requests
            if method == "tools/call"
        ]

    @property
    def closes(self) -> list[int]:
        return [upstream.closes for upstream in self.launched]


@pytest.fixture(params=REFUSING)
def mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _refused(fleet: _Fleet, tool: str = UNDECLARED) -> None:
    with pytest.raises(CapabilityBlockedError, match="capability_violation"):
        fleet.server.invoke_tool(tool, {"text": "hi"})


def _refused_without_a_start(fleet: _Fleet, tool: str = UNDECLARED) -> None:
    """A call to a server DEAD for a capability block: refused, and nothing launched."""
    launched = len(fleet.launched)
    with pytest.raises(CannotStartMcpServerError, match="not revived by a call"):
        fleet.server.invoke_tool(tool, {"text": "hi"})
    assert len(fleet.launched) == launched


def _wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# --- the start that finds the drift ------------------------------------------


def test_the_call_that_starts_the_server_is_refused_and_reaches_no_tool(mode: str):
    fleet = _Fleet(mode)

    _refused(fleet)

    assert fleet.tools_called == []
    assert fleet.closes == [1]
    assert fleet.server.state is McpServerState.DEAD
    assert fleet.server.dead_reason_snapshot == DEAD_CAPABILITY_BLOCKED
    assert fleet.server._client is None


def test_the_refusal_names_the_mode_that_refused(mode: str):
    fleet = _Fleet(mode)

    with pytest.raises(CapabilityBlockedError, match=f"enforcement_mode is {mode}"):
        fleet.server.invoke_tool(UNDECLARED, {"text": "hi"})


def test_the_blocked_start_keeps_its_violation_and_records_no_start(mode: str):
    fleet = _Fleet(mode)

    _refused(fleet)

    events = fleet.server.collect_events()
    [violation] = [e for e in events if isinstance(e, CapabilityViolationDetected)]
    assert violation.enforcement_action == mode
    assert not [e for e in events if isinstance(e, McpServerStarted)]
    transitions = [e.new_state for e in events if isinstance(e, McpServerStateChanged)]
    assert "ready" not in transitions
    assert transitions[-1] == "dead"


def test_the_blocked_start_is_a_failed_start_for_cold_start_tracking(mode: str):
    fleet = _Fleet(mode)

    _refused(fleet)

    fleet.publisher.record_cold_start.assert_not_called()
    fleet.publisher.end_cold_start.assert_called_once_with("drifting")
    assert fleet.server._ready_event.is_set()
    assert isinstance(fleet.server._start_error, CapabilityBlockedError)


def test_a_caller_waiting_on_the_blocked_start_sees_it_fail(mode: str):
    hold = threading.Event()
    fleet = _Fleet(mode, hold=hold)
    outcomes: dict[str, BaseException | None] = {}

    def start(name: str) -> None:
        try:
            fleet.server.ensure_ready()
            outcomes[name] = None
        except BaseException as exc:  # noqa: BLE001 -- the outcome is what is asserted
            outcomes[name] = exc

    starter = threading.Thread(target=start, args=("starter",))
    starter.start()
    _wait_for(lambda: bool(fleet.launched) and "tools/list" in fleet.launched[0].methods())
    waiter = threading.Thread(target=start, args=("waiter",))
    waiter.start()
    time.sleep(0.1)  # into ready_event.wait(); either way the outcome below must hold
    hold.set()
    starter.join(timeout=10)
    waiter.join(timeout=10)

    assert isinstance(outcomes["starter"], CapabilityBlockedError)
    assert isinstance(outcomes["waiter"], McpServerStartError)
    assert len(fleet.launched) == 1
    assert fleet.tools_called == []


# --- every call after it ------------------------------------------------------


def test_repeated_calls_are_refused_without_starting_the_server_again(mode: str):
    fleet = _Fleet(mode)

    _refused(fleet)
    for _ in range(2):
        _refused_without_a_start(fleet)

    assert len(fleet.launched) == 1
    assert fleet.closes == [1]
    assert fleet.tools_called == []


def test_a_declared_tool_on_the_blocked_server_is_refused_too(mode: str):
    fleet = _Fleet(mode)

    _refused(fleet, DECLARED)
    _refused_without_a_start(fleet, DECLARED)

    assert fleet.tools_called == []
    assert len(fleet.launched) == 1


def test_a_blocked_server_stays_dead_and_never_degrades(mode: str):
    # One failure is enough to degrade this server, and DEGRADED is what the
    # recovery saga retries.
    fleet = _Fleet(mode, max_consecutive_failures=1)

    _refused(fleet)

    assert fleet.server.state is McpServerState.DEAD
    assert not [e for e in fleet.server.collect_events() if isinstance(e, McpServerDegraded)]


def test_a_group_never_puts_the_blocked_member_in_rotation(mode: str):
    fleet = _Fleet(mode)
    group = McpServerGroup("pool")

    group.add_member(fleet.server)  # auto_start: the group starts it

    member = group.get_member("drifting")
    assert member is not None and member.in_rotation is False
    assert group.select_member() is None
    assert fleet.closes == [1]
    assert fleet.tools_called == []


def test_a_deliberate_start_checks_the_tools_again(mode: str):
    fleet = _Fleet(mode)
    _refused(fleet)

    # What hangar_start, the REST start and StartMcpServerCommand ask for.
    with pytest.raises(CapabilityBlockedError, match="capability_violation"):
        fleet.server.ensure_ready()

    assert len(fleet.launched) == 2
    assert fleet.closes == [1, 1]
    assert fleet.server.dead_reason_snapshot == DEAD_CAPABILITY_BLOCKED
    assert fleet.tools_called == []


def test_a_deliberate_start_serves_once_the_upstream_no_longer_drifts(mode: str):
    fleet = _Fleet(mode)
    _refused(fleet)
    fleet._tools_lists[:] = [(DECLARED,)]  # the upstream was fixed

    fleet.server.ensure_ready()
    fleet.server.invoke_tool(DECLARED, {"a": 1, "b": 2})

    assert fleet.server.state is McpServerState.READY
    assert fleet.server.dead_reason_snapshot is None
    assert fleet.tools_called == [DECLARED]
    assert fleet.closes == [1, 0]


def test_a_stop_leaves_it_cold_and_the_next_calls_start_checks_again(mode: str):
    fleet = _Fleet(mode)
    _refused(fleet)

    fleet.server.shutdown()
    assert fleet.server.state is McpServerState.COLD
    _refused(fleet, DECLARED)

    assert len(fleet.launched) == 2
    assert fleet.closes == [1, 1]
    assert fleet.tools_called == []


# --- drift that turns up after the start --------------------------------------


def test_a_tool_that_appears_on_refresh_blocks_the_server(mode: str):
    # The start sees only the declared tool; the refresh a call for an unknown
    # tool triggers sees the undeclared one too.
    fleet = _Fleet(mode, tools_lists=((DECLARED,), DRIFTED))
    fleet.server.ensure_ready()
    assert fleet.server.state is McpServerState.READY

    _refused(fleet)

    assert fleet.tools_called == []
    assert fleet.closes == [1]
    assert fleet.server.state is McpServerState.DEAD
    assert fleet.server.dead_reason_snapshot == DEAD_CAPABILITY_BLOCKED
    [violation] = [e for e in fleet.server.collect_events() if isinstance(e, CapabilityViolationDetected)]
    assert violation.enforcement_action == mode


def test_a_tool_that_list_changed_added_blocks_the_next_call_to_any_tool(mode: str):
    fleet = _Fleet(mode, tools_lists=((DECLARED,), DRIFTED))
    fleet.server.ensure_ready()

    fleet.server._refresh_tools()  # what notifications/tools/list_changed runs
    _refused(fleet, DECLARED)

    assert fleet.tools_called == []
    assert fleet.closes == [1]


# --- quarantine's own record ----------------------------------------------------


def test_quarantine_records_the_quarantine_once_when_the_start_finds_drift():
    fleet = _Fleet("quarantine")

    _refused(fleet)
    _refused_without_a_start(fleet)

    [quarantined] = [e for e in fleet.server.collect_events() if isinstance(e, McpServerCapabilityQuarantined)]
    assert quarantined.mcp_server_id == "drifting"
    # Operator-facing, like the violation event; the tool names are in that one.
    assert UNDECLARED not in quarantined.reason


def test_quarantine_records_the_quarantine_when_drift_turns_up_after_the_start():
    fleet = _Fleet("quarantine", tools_lists=((DECLARED,), DRIFTED))
    fleet.server.ensure_ready()
    fleet.server.collect_events()

    fleet.server._refresh_tools()
    _refused(fleet, DECLARED)

    assert len([e for e in fleet.server.collect_events() if isinstance(e, McpServerCapabilityQuarantined)]) == 1


def test_block_records_no_quarantine():
    fleet = _Fleet("block")

    _refused(fleet)

    assert not [e for e in fleet.server.collect_events() if isinstance(e, McpServerCapabilityQuarantined)]


# --- the checks behind ensure_ready() ------------------------------------------


def test_invoke_refuses_a_server_that_is_not_ready_even_if_ensure_ready_returned():
    # What block mode used to leave behind: DEAD, with the client still open.
    fleet = _Fleet(tools_lists=((DECLARED,),))
    fleet.server.ensure_ready()
    with fleet.server._lock:
        fleet.server._state = McpServerState.DEAD
    fleet.server.ensure_ready = lambda **_: None  # type: ignore[method-assign]

    with pytest.raises(McpServerNotReadyError):
        fleet.server.invoke_tool(DECLARED, {"a": 1, "b": 2})

    assert fleet.tools_called == []


def test_relay_refuses_a_blocked_server(mode: str):
    fleet = _Fleet(mode)
    with pytest.raises(CapabilityBlockedError):
        fleet.server.ensure_ready()

    with pytest.raises(ToolInvocationError, match="relay unavailable"):
        fleet.server.relay_request("prompts/get", {"name": "anything"})

    assert all("prompts/get" not in upstream.methods() for upstream in fleet.launched)


def test_relay_blocks_a_server_whose_catalogue_drifted_after_the_start(mode: str):
    fleet = _Fleet(mode, tools_lists=((DECLARED,), DRIFTED))
    fleet.server.ensure_ready()
    fleet.server._refresh_tools()

    with pytest.raises(ToolInvocationError, match="relay unavailable"):
        fleet.server.relay_request("resources/read", {"uri": "file:///anything"})

    [upstream] = fleet.launched
    assert "resources/read" not in upstream.methods()
    assert upstream.closes == 1
    assert fleet.server.state is McpServerState.DEAD


# --- the mode that serves anyway, and no drift at all --------------------------------


def test_alert_mode_still_serves_and_records_the_drift_after_the_start():
    fleet = _Fleet(mode="alert")

    result = fleet.server.invoke_tool(UNDECLARED, {"text": "hi"})
    fleet.server.invoke_tool(DECLARED, {"a": 1, "b": 2})

    assert result == {"content": [{"type": "text", "text": "exfiltrate ran"}]}
    assert fleet.tools_called == [UNDECLARED, DECLARED]
    assert fleet.server.state is McpServerState.READY
    assert fleet.closes == [0]
    order = [
        type(e).__name__
        for e in fleet.server.collect_events()
        if isinstance(e, (McpServerStarted, CapabilityViolationDetected, McpServerCapabilityQuarantined))
    ]
    assert order == ["McpServerStarted", "CapabilityViolationDetected"]


def test_a_refusing_mode_without_drift_serves(mode: str):
    fleet = _Fleet(mode, tools_lists=((DECLARED,),))

    fleet.server.invoke_tool(DECLARED, {"a": 1, "b": 2})

    assert fleet.tools_called == [DECLARED]
    assert fleet.server.state is McpServerState.READY
    events = fleet.server.collect_events()
    assert not [e for e in events if isinstance(e, (CapabilityViolationDetected, McpServerCapabilityQuarantined))]
