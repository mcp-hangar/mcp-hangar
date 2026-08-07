"""What a long outage looks like in the log: once, then occasionally, then over.

Both of these were found by taking the database away from a real three-replica
deployment and reading what came out, not by a test.

The tailer logged **every** failed read -- thirty lines a minute per replica,
which is the volume at which an operator stops reading. The keeper logged its
failed acquisitions at `debug`, which at a production log level is silence: an
instance that could not reach the store never became the manager and never said
so, so a fleet with nothing converging it looked exactly like a fleet with
nothing to do.

The recovery line matters as much as the failure one. "It started working again"
is the fact an operator most often has to establish from absence.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.services.log_pacing import RepeatedFailure


class TestReportingARunOfFailures:
    def test_the_first_one_is_reported(self) -> None:
        assert RepeatedFailure().failed() is True

    def test_the_next_ones_are_not(self) -> None:
        run = RepeatedFailure(every=30)
        run.failed()

        assert [run.failed() for _ in range(28)] == [False] * 28

    def test_one_in_every_is_reported_so_it_does_not_go_quiet(self) -> None:
        # An outage that lasts an hour should still be visible in the last
        # minute of logs, not only in the first.
        run = RepeatedFailure(every=30)

        reported = sum(1 for _ in range(90) if run.failed())

        # The first, then the 30th, 60th and 90th: four, not three. Written out
        # because getting it wrong is how a "one in thirty" rule quietly becomes
        # one in sixty.
        assert reported == 4

    def test_it_counts_the_run_for_the_message(self) -> None:
        # "failed once" and "failed for the four hundredth time" are different
        # facts, and the second one is the one that gets acted on.
        run = RepeatedFailure(every=30)
        for _ in range(30):
            run.failed()

        assert run.run_length == 30


class TestReportingTheEnd:
    def test_recovery_after_a_failure_is_reported(self) -> None:
        run = RepeatedFailure()
        run.failed()

        assert run.recovered() is True

    def test_a_loop_that_was_never_failing_stays_quiet(self) -> None:
        # Otherwise every healthy poll would announce its own success.
        assert RepeatedFailure().recovered() is False

    def test_the_next_failure_starts_a_new_run(self) -> None:
        run = RepeatedFailure(every=30)
        run.failed()
        run.recovered()

        assert run.failed() is True


class TestTheTwoLoopsUseIt:
    def test_the_keeper_reports_an_unreachable_store_at_warning(self) -> None:
        # It was `debug`, which is silence where it matters.
        import inspect

        from mcp_hangar.application.services import lease_keeper

        source = inspect.getsource(lease_keeper.ManagementLeaseKeeper._try_acquire)

        assert "logger.warning" in source
        assert "logger.debug" not in source

    def test_the_tailer_does_not_report_every_failed_read(self) -> None:
        import inspect

        from mcp_hangar.application.services import event_tailer

        source = inspect.getsource(event_tailer.EventTailer._loop)

        assert "_read_failures.failed()" in source

    @pytest.mark.parametrize(
        "module, attribute",
        [("lease_keeper", "_acquire_failures"), ("event_tailer", "_read_failures")],
    )
    def test_both_say_when_it_clears(self, module, attribute) -> None:
        import importlib
        import inspect

        source = inspect.getsource(importlib.import_module(f"mcp_hangar.application.services.{module}"))

        assert f"{attribute}.recovered()" in source
