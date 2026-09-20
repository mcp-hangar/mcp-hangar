"""A restart the server refuses for its backoff is rescheduled, not dropped (#1401).

The saga's restarts run on its own clock, 5s then 10s then 20s, and the
server's backoff after three failures is 8s, then 16s, then 32s. So with
default settings the server refused the first restart, the refusal recorded
nothing, and nothing scheduled another. The saga never restarted anything.

Now the saga hears the refusal through ``schedule_command(on_failure=...)`` and
schedules the restart again at the retry time the server reports, the
``time_until_retry`` that ``CannotStartMcpServerError`` carries. The refusal is
not one of the saga's attempts. The server's backoff is still the only clock
that decides when a start may run.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.commands import Command, GiveUpOnMcpServerCommand, StartMcpServerCommand
from mcp_hangar.application.sagas.mcp_server_recovery_saga import (
    MIN_RESCHEDULE_DELAY_S,
    RESCHEDULE_MARGIN_S,
    McpServerRecoverySaga,
)
from mcp_hangar.domain.events import McpServerDegraded, McpServerStarted, McpServerStopped
from mcp_hangar.domain.exceptions import CannotStartMcpServerError, McpServerStartError
from mcp_hangar.infrastructure.saga_manager import SagaManager

SID = "svc"
GIVE_UP = GiveUpOnMcpServerCommand(mcp_server_id=SID, reason="max_retries_exceeded")


def _refused(retry_in_s: float) -> CannotStartMcpServerError:
    return CannotStartMcpServerError(SID, "backoff not elapsed", retry_in_s)


@dataclass
class _Scheduled:
    command: Command
    delay_s: float
    on_failure: Callable[[Exception], list[Command]] | None
    timer_id: str
    cancelled: bool = False
    fired: bool = False


@dataclass
class _Scheduler:
    """A saga manager whose timers fire when the test says, with the outcome it says.

    As in ``SagaManager``, a timer that fired has left the registry: it is no
    longer live, and cancelling it does nothing.
    """

    scheduled: list[_Scheduled] = field(default_factory=list)

    def schedule_command(
        self,
        command: Command,
        delay_s: float,
        *,
        on_failure: Callable[[Exception], list[Command]] | None = None,
    ) -> str:
        timer_id = f"t{len(self.scheduled) + 1}"
        self.scheduled.append(_Scheduled(command, delay_s, on_failure, timer_id))
        return timer_id

    def cancel_scheduled_command(self, timer_id: str) -> bool:
        for entry in self.live:
            if entry.timer_id == timer_id:
                entry.cancelled = True
                return True
        return False

    @property
    def live(self) -> list[_Scheduled]:
        return [entry for entry in self.scheduled if not (entry.cancelled or entry.fired)]

    def last(self) -> _Scheduled:
        return self.scheduled[-1]

    def fail(self, entry: _Scheduled, error: Exception) -> list[Command]:
        """The timer fires and the command raises ``error``: what the manager does next."""
        assert not entry.cancelled, "a cancelled restart fired"
        assert entry.on_failure is not None, "a restart scheduled without a failure hook: a refusal would be lost"
        entry.fired = True
        return entry.on_failure(error)


def _saga(**kwargs: Any) -> tuple[McpServerRecoverySaga, _Scheduler]:
    scheduler = _Scheduler()
    return McpServerRecoverySaga(saga_manager=scheduler, **kwargs), scheduler  # type: ignore[arg-type]


def _degrade(saga: McpServerRecoverySaga, failures: int = 3) -> list[Command]:
    return saga.handle(McpServerDegraded(SID, failures, failures, "health_checks"))


class TestARefusal:
    def test_is_rescheduled_at_the_retry_time_the_server_reports(self):
        saga, scheduler = _saga()
        _degrade(saga)
        first = scheduler.last()
        assert (first.command, first.delay_s) == (StartMcpServerCommand(mcp_server_id=SID), 5.0)

        follow_ups = scheduler.fail(first, _refused(2.6))

        again = scheduler.last()
        assert follow_ups == []
        assert again is not first
        assert again.command == StartMcpServerCommand(mcp_server_id=SID)
        assert again.delay_s == pytest.approx(2.6 + RESCHEDULE_MARGIN_S)

    def test_is_not_counted_as_an_attempt(self):
        saga, scheduler = _saga(max_retries=1)
        _degrade(saga)

        for _ in range(5):
            scheduler.fail(scheduler.last(), _refused(3.0))

        assert saga.get_retry_state(SID)["retries"] == 1
        # Still recovering: the sixth restart waits, and nothing gave up.
        assert len(scheduler.scheduled) == 6
        assert scheduler.live[-1] is scheduler.last()

    def test_moves_the_next_retry_it_reports(self):
        saga, scheduler = _saga()
        _degrade(saga)

        scheduler.fail(scheduler.last(), _refused(7.0))

        expected = time.time() + 7.0 + RESCHEDULE_MARGIN_S
        assert saga.get_retry_state(SID)["next_retry"] == pytest.approx(expected, abs=1.0)

    # The next three: the restart was already firing when recovery ended, so
    # the cancel reached no timer, and the refusal arrives after it.
    def test_after_the_server_started_is_not_rescheduled(self):
        # Refused, and meanwhile a call started the server: recovery is over.
        saga, scheduler = _saga()
        _degrade(saga)
        first = scheduler.last()
        first.fired = True
        saga.handle(McpServerStarted(SID, "subprocess", 1, 1.0))

        assert first.on_failure is not None and first.on_failure(_refused(3.0)) == []
        assert scheduler.live == []

    @pytest.mark.parametrize("reason", ["shutdown", "user_request"])
    def test_after_a_stop_is_not_rescheduled(self, reason):
        # A restart rescheduled past an operator's stop would undo it.
        saga, scheduler = _saga()
        _degrade(saga)
        first = scheduler.last()
        first.fired = True
        saga.handle(McpServerStopped(SID, reason))

        assert first.on_failure is not None and first.on_failure(_refused(3.0)) == []
        assert scheduler.live == []

    def test_after_the_give_up_is_not_rescheduled(self):
        saga, scheduler = _saga(max_retries=1)
        _degrade(saga, 3)
        first = scheduler.last()
        first.fired = True
        assert _degrade(saga, 4) == [GIVE_UP]

        assert first.on_failure is not None and first.on_failure(_refused(3.0)) == []
        assert scheduler.live == []

    def test_waiting_to_be_tried_again_is_superseded_by_the_next_attempt(self):
        # Refused because another start was under way; that start fails and
        # degrades the server. One restart follows, not two.
        saga, scheduler = _saga()
        _degrade(saga, 3)
        scheduler.fail(scheduler.last(), _refused(30.0))
        _degrade(saga, 4)

        [only] = scheduler.live
        assert only.delay_s == 10.0
        assert saga.get_retry_state(SID)["retries"] == 2


class TestAStartThatRanAndFailed:
    def test_is_counted_once_through_its_degrade_event(self):
        # The failed start records McpServerDegraded, which counts it. The
        # exception the timer then sees must not count it again.
        saga, scheduler = _saga()
        _degrade(saga, 3)
        first = scheduler.last()
        first.fired = True
        _degrade(saga, 4)  # published inside the failed start, before its exception reaches the timer

        assert first.on_failure is not None
        follow_ups = first.on_failure(McpServerStartError(SID, "upstream exited"))

        assert follow_ups == []
        assert saga.get_retry_state(SID)["retries"] == 2
        [next_restart] = scheduler.live
        assert next_restart.delay_s == 10.0


class TestTheBudget:
    def test_gives_up_after_max_retries_real_attempts_however_many_were_refused(self):
        saga, scheduler = _saga(max_retries=3)
        _degrade(saga, 3)

        commands: list[Command] = []
        for failures in (4, 5, 6):
            # Refused twice while the server's backoff runs, then it runs and fails.
            scheduler.fail(scheduler.last(), _refused(4.0))
            scheduler.fail(scheduler.last(), _refused(0.4))
            assert saga.get_retry_state(SID)["retries"] == failures - 3
            scheduler.last().fired = True
            commands = _degrade(saga, failures)  # the failed start's degrade counts it

        assert commands == [GIVE_UP]
        assert scheduler.live == [], "a restart left armed after the give-up"


class TestTheLoopGuard:
    @pytest.mark.parametrize("retry_in_s", [0.0, 0.1, -5.0, float("nan")])
    def test_a_retry_time_near_zero_or_below_waits_the_minimum(self, retry_in_s):
        saga, scheduler = _saga()
        _degrade(saga)

        scheduler.fail(scheduler.last(), _refused(retry_in_s))

        assert scheduler.last().delay_s == MIN_RESCHEDULE_DELAY_S

    def test_a_retry_time_past_the_saga_ceiling_waits_the_ceiling(self):
        saga, scheduler = _saga(max_backoff_s=60.0)
        _degrade(saga)

        scheduler.fail(scheduler.last(), _refused(float("inf")))

        assert scheduler.last().delay_s == 60.0

    def test_refusals_past_the_bound_count_as_a_failed_attempt(self):
        saga, scheduler = _saga(max_retries=3, max_refusals=4)
        _degrade(saga)

        for _ in range(4):
            scheduler.fail(scheduler.last(), _refused(0.0))
        assert saga.get_retry_state(SID)["retries"] == 1
        scheduler.fail(scheduler.last(), _refused(0.0))

        assert saga.get_retry_state(SID)["retries"] == 2
        [next_restart] = scheduler.live
        assert next_restart.delay_s == 10.0, "the next attempt runs on the saga's own backoff"

    def test_a_server_that_always_refuses_is_given_up_on(self):
        # Bounded: max_retries attempts of max_refusals refusals each, then the
        # give-up -- never a loop that polls forever.
        saga, scheduler = _saga(max_retries=2, max_refusals=3)
        _degrade(saga)

        commands: list[Command] = []
        for _ in range(100):  # far past the bound; a loop that never ends fails here, not in the timeout
            if not scheduler.live:
                break
            [waiting] = scheduler.live
            commands = scheduler.fail(waiting, _refused(0.0))

        assert commands == [GIVE_UP]
        assert scheduler.live == []
        assert len(scheduler.scheduled) == 2 * (3 + 1)


class _Bus:
    """A command bus whose ``send`` runs ``outcome`` and remembers the timer thread it ran on."""

    def __init__(self, outcome: Callable[[Command], Any]):
        self.outcome = outcome
        self.threads: list[threading.Thread] = []
        self.sent: list[Command] = []

    def send(self, command: Command) -> Any:
        self.threads.append(threading.current_thread())
        self.sent.append(command)
        return self.outcome(command)

    def join(self) -> None:
        deadline = time.monotonic() + 2.0
        while not self.threads and time.monotonic() < deadline:
            time.sleep(0.01)
        for thread in self.threads:
            thread.join(2.0)
            assert not thread.is_alive()


def _raise(error: Exception) -> Callable[[Command], Any]:
    def outcome(command: Command) -> Any:
        if isinstance(command, StartMcpServerCommand):
            raise error
        return None

    return outcome


class TestTheSagaManager:
    """The hook on the real manager: a real timer and a command bus that raises."""

    def test_passes_the_error_to_the_hook_and_sends_what_it_returns(self):
        refusal = _refused(1.0)
        bus = _Bus(_raise(refusal))
        manager = SagaManager(command_bus=bus, event_bus=MagicMock())  # type: ignore[arg-type]
        hook = MagicMock(return_value=[GIVE_UP])

        manager.schedule_command(StartMcpServerCommand(mcp_server_id=SID), delay_s=0.0, on_failure=hook)
        bus.join()

        hook.assert_called_once_with(refusal)
        assert bus.sent == [StartMcpServerCommand(mcp_server_id=SID), GIVE_UP]

    def test_a_hook_may_schedule_again_and_shutdown_cancels_that(self):
        bus = _Bus(_raise(_refused(1.0)))
        manager = SagaManager(command_bus=bus, event_bus=MagicMock())  # type: ignore[arg-type]

        def reschedule(error: Exception) -> list[Command]:
            manager.schedule_command(StartMcpServerCommand(mcp_server_id=SID), delay_s=30.0)
            return []

        manager.schedule_command(StartMcpServerCommand(mcp_server_id=SID), delay_s=0.0, on_failure=reschedule)
        bus.join()

        assert manager.cancel_all_scheduled_commands() == 1  # the rescheduled one, still waiting
        assert manager._pending_timers == {}

    def test_a_cancel_while_the_command_was_firing_stops_its_follow_up(self):
        # Shutdown cancels every timer (#1389). One already firing is out of
        # the registry, so what its failure would schedule must not arm.
        firing, cancelled = threading.Event(), threading.Event()

        def outcome(command: Command) -> Any:
            firing.set()
            assert cancelled.wait(2.0)
            raise _refused(1.0)

        bus = _Bus(outcome)
        manager = SagaManager(command_bus=bus, event_bus=MagicMock())  # type: ignore[arg-type]
        hook = MagicMock(return_value=[])
        manager.schedule_command(StartMcpServerCommand(mcp_server_id=SID), delay_s=0.0, on_failure=hook)

        assert firing.wait(2.0)
        manager.cancel_all_scheduled_commands()
        cancelled.set()
        bus.join()

        hook.assert_not_called()

    def test_a_failing_hook_does_not_crash_the_timer_thread(self):
        bus = _Bus(_raise(_refused(1.0)))
        manager = SagaManager(command_bus=bus, event_bus=MagicMock())  # type: ignore[arg-type]
        hook = MagicMock(side_effect=RuntimeError("boom"))

        manager.schedule_command(StartMcpServerCommand(mcp_server_id=SID), delay_s=0.0, on_failure=hook)
        bus.join()

        hook.assert_called_once()
        assert bus.sent == [StartMcpServerCommand(mcp_server_id=SID)]


class TestTheSagaOnTheRealManager:
    def test_a_refused_restart_is_tried_again_and_then_runs(self):
        # End to end through real timers: refused once, rescheduled at the
        # reported time (floored at the minimum), then it runs.
        outcomes: list[Exception | None] = [_refused(0.0), None]
        ran = threading.Event()

        def outcome(command: Command) -> Any:
            result = outcomes.pop(0)
            if result is not None:
                raise result
            ran.set()
            return None

        bus = _Bus(outcome)
        manager = SagaManager(command_bus=bus, event_bus=MagicMock())  # type: ignore[arg-type]
        saga = McpServerRecoverySaga(initial_backoff_s=0.0, saga_manager=manager)

        _degrade(saga)

        assert ran.wait(MIN_RESCHEDULE_DELAY_S + 3.0)
        assert bus.sent == [StartMcpServerCommand(mcp_server_id=SID)] * 2
        assert saga.get_retry_state(SID)["retries"] == 1
        manager.cancel_all_scheduled_commands()
