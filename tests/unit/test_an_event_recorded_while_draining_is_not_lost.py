"""An event recorded while another thread drains the aggregate is published, not dropped (#1400).

`collect_events()` copied the pending list and then cleared it. An event
appended between the two was cleared without being returned, so nothing ever
published it. Threads do exactly that: the health worker drains a server's
events on its own loop while a recovery saga's scheduled restart records a
`McpServerDegraded` on another. The served-path test for #1361 lost one that
way, and the saga, which counts those events, never gave the server up.
"""

from __future__ import annotations

import sys
import threading

from mcp_hangar.domain.events import McpServerStopped
from mcp_hangar.domain.model.aggregate import AggregateRoot

RECORDED = 20_000


class _Aggregate(AggregateRoot):
    def record(self, n: int) -> None:
        self._record_event(McpServerStopped(mcp_server_id="svc", reason=str(n)))


def test_every_event_recorded_during_draining_is_collected_exactly_once():
    aggregate = _Aggregate()
    collected: list[str] = []
    done = threading.Event()

    def drain() -> None:
        while not done.is_set():
            collected.extend(e.reason for e in aggregate.collect_events())

    def record() -> None:
        for n in range(RECORDED):
            aggregate.record(n)
        done.set()

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # interleave the two threads as finely as the interpreter will
    try:
        threads = [threading.Thread(target=drain), threading.Thread(target=record)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        sys.setswitchinterval(interval)
    collected.extend(e.reason for e in aggregate.collect_events())

    assert collected == [str(n) for n in range(RECORDED)], "an event was dropped, duplicated or reordered"


def test_a_single_thread_gets_the_same_events_in_the_same_order():
    aggregate = _Aggregate()
    for n in range(3):
        aggregate.record(n)

    assert [e.reason for e in aggregate.collect_events()] == ["0", "1", "2"]
    assert aggregate.collect_events() == []
    assert aggregate.has_uncommitted_events() is False
