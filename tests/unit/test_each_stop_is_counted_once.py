"""Each server stop is counted once, under the reason it was made for (#1466).

`mcp_hangar_mcp_server_stops_total` counts a stop from the `McpServerStopped`
the server records, and from nowhere else. The GC counted an idle reap itself
as well, so each reap added 2. The stop command counted its own reason, and its
event added a `shutdown`, so one stop read as two, under two reasons.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

import mcp_hangar
from mcp_hangar import metrics as m
from mcp_hangar.application.commands import GiveUpOnMcpServerCommand, StartMcpServerCommand, StopMcpServerCommand
from mcp_hangar.application.commands.handlers import (
    GiveUpOnMcpServerHandler,
    StartMcpServerHandler,
    StopMcpServerHandler,
)
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import STOPPED_BY_GIVING_UP, DomainEvent, McpServerStopped
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.gc import BackgroundWorker
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.stream_ids import MCP_SERVER

#: The reasons the stop command is sent with: hangar_stop and the REST stop
#: (`user_request`), a REST stop with an empty reason (`manual`), the failover
#: saga, and the REST block or a detection rule.
_COMMAND_REASONS = ("user_request", "manual", "failback", "compensation", "detection_enforcement:block")


class _Upstream:
    """A connection that answers until `failing` is set."""

    def __init__(self) -> None:
        self.failing = False
        self.closed = False
        self.modern_envelope = False

    def is_alive(self) -> bool:
        return not self.closed

    def call(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-11-25"}}
        if method == "tools/list" and not self.failing:
            return {"result": {"tools": [{"name": "add"}]}}
        return {"error": {"code": -32603, "message": "upstream is down"}}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Server:
    """One ready server, and a bus that feeds /metrics as the running app's does."""

    def __init__(self) -> None:
        self.upstream = _Upstream()
        self.server = McpServer(
            mcp_server_id=f"svc-{uuid4().hex[:12]}",
            mode="subprocess",
            command=["unused"],
            idle_ttl_s=60,
            max_consecutive_failures=1,
            metrics_publisher=Mock(),
        )
        self.server._create_client = lambda: self.upstream  # type: ignore[method-assign]
        self.events = EventBus()
        self.events.subscribe_to_all(MetricsEventHandler().handle, kind=HandlerKind.EFFECT)
        self.published: list[DomainEvent] = []
        self.events.subscribe_to_all(self.published.append, kind=HandlerKind.EFFECT)
        repository = Mock(get=Mock(return_value=self.server))
        self.bus = CommandBus()
        self.bus.register(StartMcpServerCommand, StartMcpServerHandler(repository, self.events))
        self.bus.register(StopMcpServerCommand, StopMcpServerHandler(repository, self.events))
        self.bus.register(GiveUpOnMcpServerCommand, GiveUpOnMcpServerHandler(repository, self.events))
        self.bus.send(StartMcpServerCommand(mcp_server_id=self.sid))
        assert self.server.state is McpServerState.READY

    @property
    def sid(self) -> str:
        return self.server.mcp_server_id

    def stops(self) -> dict[str, float]:
        """Every stop-counter sample for this server, by reason."""
        return {
            sample.labels["reason"]: float(sample.value)
            for sample in m.PROVIDER_STOPS_TOTAL.collect()
            if sample.labels.get("mcp_server") == self.sid
        }

    def stop_reasons(self) -> list[str]:
        """The reason of every stop the server recorded."""
        return [event.reason for event in self.published if isinstance(event, McpServerStopped)]

    def _publish(self) -> None:
        events = list(self.server.collect_events())
        if events:
            self.events.publish_aggregate_events(MCP_SERVER, self.sid, events)

    # The ways a server stops.

    def reap_idle(self) -> None:
        """One GC cycle, with the server unused past its idle TTL."""
        self.server._last_used = time.time() - 3600
        worker = BackgroundWorker({self.sid: self.server}, interval_s=1, task="gc", event_bus=self.events)
        worker.running = True
        with patch.object(worker._stopped, "wait", side_effect=[False, StopIteration]), pytest.raises(StopIteration):
            worker._loop()

    def stop(self, **reason: str) -> None:
        """hangar_stop, the REST stop or block, a failover saga's stop."""
        self.bus.send(StopMcpServerCommand(mcp_server_id=self.sid, **reason))

    def shut_down(self) -> None:
        """A reload, an unload or delete, a group's stop_all, process exit."""
        self.server.shutdown()
        self._publish()

    def give_up(self) -> None:
        """The recovery saga, out of retries on a degraded server."""
        self.upstream.failing = True
        while self.server.state is not McpServerState.DEGRADED:
            self.server.health_check()
        self._publish()
        self.bus.send(GiveUpOnMcpServerCommand(mcp_server_id=self.sid, reason=STOPPED_BY_GIVING_UP))


def test_one_idle_reap_adds_one_under_idle():
    server = _Server()

    server.reap_idle()

    assert server.server.state is McpServerState.COLD
    assert server.stop_reasons() == ["idle"]
    assert server.stops() == {"idle": 1.0}


@pytest.mark.parametrize("reason", _COMMAND_REASONS)
def test_one_stop_command_adds_one_under_its_own_reason_and_none_under_shutdown(reason):
    server = _Server()

    server.stop(reason=reason)

    assert server.stop_reasons() == [reason]
    assert server.stops() == {reason: 1.0}


def test_hangar_stop_is_one_user_request():
    server = _Server()

    server.stop()

    assert server.stops() == {"user_request": 1.0}


@pytest.mark.parametrize("given", ["maintenance window", ""])
def test_a_stop_for_any_other_reason_is_one_manual_stop(given):
    server = _Server()

    server.stop(reason=given)

    assert server.stop_reasons() == ["manual"]
    assert server.stops() == {"manual": 1.0}


@pytest.mark.parametrize(
    "make_stop",
    [_Server.reap_idle, _Server.stop, _Server.shut_down, _Server.give_up],
    ids=["idle reap", "stop command", "shutdown", "give-up"],
)
def test_every_stop_is_one_event_and_one_count(make_stop):
    server = _Server()

    make_stop(server)

    assert len(server.stop_reasons()) == 1
    assert sum(server.stops().values()) == 1.0


_STOP_COUNTER = {"record_mcp_server_stop", "PROVIDER_STOPS_TOTAL"}


def _counts_stops(source: Path) -> bool:
    text = source.read_text(encoding="utf-8")
    if not any(name in text for name in _STOP_COUNTER):
        return False
    return any(
        (isinstance(node, ast.Name) and node.id in _STOP_COUNTER)
        or (isinstance(node, ast.Attribute) and node.attr in _STOP_COUNTER)
        for node in ast.walk(ast.parse(text))
    )


def test_only_the_stop_event_handler_counts_a_stop():
    # A stop counted where it is made is counted again from its event.
    package = Path(mcp_hangar.__file__).parent
    counting = sorted(
        source.relative_to(package).as_posix()
        for source in package.rglob("*.py")
        if source != package / "metrics.py" and _counts_stops(source)
    )

    assert counting == ["infrastructure/observability/metrics_event_handler.py"]
