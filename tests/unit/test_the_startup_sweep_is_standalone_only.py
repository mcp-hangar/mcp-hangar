"""The startup sweep runs where it is correct, and nowhere else.

`dispatch_pending` reads the log from one shared mark and hands everything past
it to local handlers. That is right when this process is the only writer of that
log, and wrong the moment it is not -- in both directions at once:

- It re-delivers *peers'* events to this instance's handlers. A second export to
  the SIEM, a second cost record, for work another replica already accounted for.
- The mark is advanced by whichever replica publishes next, so it moves past
  events a *different* replica appended and never delivered. The sweep then
  skips exactly what it exists to recover.

The recovery did not disappear, it moved: effects follow the instance that
produced the event, which is exactly-once by construction because a tool call
happens on exactly one replica (#790, phase 0.4).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_hangar.server.bootstrap.event_store import recover_undelivered_events


class _Bus:
    def __init__(self, pending: int = 3) -> None:
        self.pending = pending
        self.swept = 0

    def dispatch_pending(self) -> int:
        self.swept += 1
        return self.pending


@pytest.fixture
def no_backend(monkeypatch):
    from mcp_hangar.server.bootstrap import composition

    monkeypatch.setattr(composition, "_persistence_backend", None)


@pytest.fixture
def a_backend(monkeypatch):
    from mcp_hangar.server.bootstrap import composition

    monkeypatch.setattr(composition, "_persistence_backend", object())


class TestStandalone:
    def test_the_sweep_runs(self, no_backend) -> None:
        # The window it closes is real and was open forever: a process that died
        # between the append and the handler left events durably stored and
        # never delivered, with nothing that would ever look again.
        bus = _Bus(pending=3)

        assert recover_undelivered_events(SimpleNamespace(event_bus=bus)) == 3
        assert bus.swept == 1


class TestWithAStorageBackendSelected:
    def test_the_sweep_does_not_run(self, a_backend) -> None:
        bus = _Bus(pending=3)

        assert recover_undelivered_events(SimpleNamespace(event_bus=bus)) == 0
        assert bus.swept == 0, "the sweep re-delivered a peer's events to this instance's handlers"

    def test_it_is_reported_rather_than_silently_skipped(self, a_backend, monkeypatch) -> None:
        # An operator upgrading to a shared backend loses a startup behaviour.
        # Losing it quietly is how a behaviour change becomes a mystery six
        # months later.
        from mcp_hangar.server.bootstrap import event_store

        said: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            event_store,
            "logger",
            SimpleNamespace(info=lambda event, **kw: said.append((event, kw)), warning=lambda *a, **k: None),
        )

        recover_undelivered_events(SimpleNamespace(event_bus=_Bus()))

        assert [event for event, _ in said] == ["dispatch_recovery_skipped"]
        assert "peers" in said[0][1]["detail"]


class TestTheDecisionIsWrittenDownWhereItIsRead:
    def test_the_contract_says_which_mark_this_is(self) -> None:
        # The next person to touch this will be looking at one number that used
        # to mean two things. The reason it now means one belongs next to it.
        from mcp_hangar.domain.contracts import dispatch_checkpoint

        doc = dispatch_checkpoint.__doc__ or ""

        assert "standalone" in doc.lower()
        assert "produced" in doc.lower()
