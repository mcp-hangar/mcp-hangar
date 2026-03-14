"""Integration tests for saga compensation in ProviderFailoverSaga.

Tests exercise:
    (a) Successful failover end-to-end: all steps complete, saga reaches COMPLETED.
    (b) Mid-saga failure: step 2 fails, _compensate_saga() runs, all eligible
        compensation commands are dispatched in reverse step order.
    (c) Full compensation path: a compensated saga reaches SagaState.COMPENSATED.
"""

from unittest.mock import MagicMock

from mcp_hangar.application.commands import StartProviderCommand, StopProviderCommand
from mcp_hangar.application.sagas.provider_failover_saga import ProviderFailoverSaga
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.saga_manager import SagaManager, SagaState, set_saga_manager


def _make_manager(command_bus: CommandBus) -> SagaManager:
    """Create an isolated SagaManager with a real EventBus and injected CommandBus."""
    event_bus = EventBus()
    manager = SagaManager(command_bus=command_bus, event_bus=event_bus)
    set_saga_manager(manager)
    return manager


class TestProviderFailoverSagaHappyPath:
    """(a) Successful failover end-to-end."""

    def test_all_steps_complete_saga_reaches_completed(self):
        """Happy path: every step command succeeds -> SagaState.COMPLETED."""
        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.return_value = None

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        assert context.state == SagaState.COMPLETED, f"Expected COMPLETED but got {context.state}"
        assert context.error is None

    def test_all_three_steps_are_marked_completed(self):
        """Happy path: all three steps have completed=True after execution."""
        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.return_value = None

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        steps = saga.steps
        assert len(steps) == 3
        assert steps[0].completed is True, "start_backup step should be completed"
        assert steps[1].completed is True, "await_primary step should be completed"
        assert steps[2].completed is True, "failback step should be completed"

    def test_start_backup_command_dispatched_to_correct_provider(self):
        """Happy path: start_backup step sends StartProviderCommand for backup_id."""
        dispatched: list = []

        def capture_send(cmd):
            dispatched.append(cmd)

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = capture_send

        manager = _make_manager(mock_command_bus)
        manager.start_saga(
            ProviderFailoverSaga(),
            initial_data={"primary_id": "primary", "backup_id": "backup"},
        )

        # Step 0 (start_backup) and step 2 (failback) both dispatch commands.
        # Step 1 (await_primary) has no command.
        assert len(dispatched) == 2
        assert isinstance(dispatched[0], StartProviderCommand)
        assert dispatched[0].provider_id == "backup"
        assert isinstance(dispatched[1], StopProviderCommand)
        assert dispatched[1].provider_id == "backup"
        assert dispatched[1].reason == "failback"


class TestProviderFailoverSagaMidSagaFailure:
    """(b) Mid-saga failure triggers compensation in reverse step order."""

    def test_step2_failure_triggers_compensate(self):
        """If the failback step (step 2) fails, compensation must run."""
        call_count = {"n": 0}

        def fail_on_third_call(cmd):
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Third send = step 2 (failback) -- fail it
                raise RuntimeError("failback_command_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_on_third_call

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        # Saga must not remain in RUNNING state after failure
        assert context.state in (SagaState.COMPENSATED, SagaState.COMPENSATING, SagaState.FAILED)

    def test_step2_failure_compensation_dispatches_stop_for_backup(self):
        """If step 2 fails, the start_backup compensation (StopProviderCommand) is dispatched."""
        dispatched: list = []
        call_count = {"n": 0}

        def side_effect(cmd):
            call_count["n"] += 1
            dispatched.append(("send", cmd))
            if call_count["n"] == 2:
                raise RuntimeError("failback_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = side_effect

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        sent_cmds = [cmd for (_label, cmd) in dispatched]

        # Compensation for start_backup: StopProviderCommand(backup, reason="compensation")
        compensation_cmds = [c for c in sent_cmds if isinstance(c, StopProviderCommand) and c.reason == "compensation"]
        assert len(compensation_cmds) >= 1, (
            "Expected at least one StopProviderCommand(reason='compensation') to be dispatched"
        )
        assert compensation_cmds[0].provider_id == "backup"

    def test_step2_failure_compensation_is_reverse_order(self):
        """Compensation commands must be dispatched in reverse step order."""
        dispatched: list = []
        call_count = {"n": 0}

        def side_effect(cmd):
            call_count["n"] += 1
            dispatched.append(cmd)
            if call_count["n"] == 2:
                raise RuntimeError("failback_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = side_effect

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        # dispatched[0] = StartProviderCommand(backup) -- step 0 forward
        # dispatched[1] = StopProviderCommand(backup, reason="failback") -- step 2 forward (fails)
        # dispatched[2] = StopProviderCommand(backup, reason="compensation") -- step 0 compensation
        #
        # Step 1 (await_primary) has no command and no compensation, so it is skipped.
        # The forward commands for step 0 and step 2 plus the compensation of step 0:
        assert len(dispatched) >= 2

        # The last dispatched command after the failure must be the compensation
        # for start_backup (StopProviderCommand with reason="compensation").
        last_cmd = dispatched[-1]
        assert isinstance(last_cmd, StopProviderCommand)
        assert last_cmd.reason == "compensation"
        assert last_cmd.provider_id == "backup"

    def test_step1_failure_no_compensation_needed(self):
        """If step 0 (start_backup) fails immediately, compensation list is empty (nothing completed yet)."""
        dispatched: list = []
        call_count = {"n": 0}

        def fail_first_call(cmd):
            call_count["n"] += 1
            dispatched.append(cmd)
            if call_count["n"] == 1:
                raise RuntimeError("start_backup_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_first_call

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        # Only the one failed start_backup command was dispatched; no compensation possible
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], StartProviderCommand)

        # Saga must reach COMPENSATED (empty compensation list still transitions)
        assert context.state == SagaState.COMPENSATED


class TestProviderFailoverSagaCompensatedState:
    """(c) Full compensation path reaches SagaState.COMPENSATED."""

    def test_mid_saga_failure_reaches_compensated(self):
        """After a mid-saga failure and compensation, the saga must be in COMPENSATED state."""
        call_count = {"n": 0}

        def fail_on_second(cmd):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("step2_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_on_second

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        assert context.state == SagaState.COMPENSATED

    def test_compensated_saga_records_error(self):
        """A compensated saga context must include the error that triggered compensation."""
        call_count = {"n": 0}

        def fail_on_second(cmd):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("intentional_failure")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_on_second

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        assert context.error is not None
        assert "intentional_failure" in context.error

    def test_compensated_saga_moves_to_history(self):
        """After compensation, the saga context must appear in SagaManager history."""
        call_count = {"n": 0}

        def fail_on_second(cmd):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("step2_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_on_second

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        history = manager.get_saga_history()
        history_ids = [c.saga_id for c in history]
        assert context.saga_id in history_ids

    def test_compensated_saga_not_in_active_sagas(self):
        """After compensation, the saga must no longer be in the active sagas list."""
        call_count = {"n": 0}

        def fail_on_second(cmd):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("step2_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_on_second

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        active = manager.get_active_sagas()
        active_ids = [c.saga_id for c in active]
        assert context.saga_id not in active_ids

    def test_compensation_failure_still_reaches_compensated(self):
        """Even if a compensation command fails, the saga must still reach COMPENSATED."""
        call_count = {"n": 0}

        def fail_on_second_and_third(cmd):
            call_count["n"] += 1
            # fail step 2 (forward) AND step 0 compensation
            if call_count["n"] >= 2:
                raise RuntimeError("command_failed")

        mock_command_bus = MagicMock(spec=CommandBus)
        mock_command_bus.send.side_effect = fail_on_second_and_third

        manager = _make_manager(mock_command_bus)
        saga = ProviderFailoverSaga()

        context = manager.start_saga(saga, initial_data={"primary_id": "primary", "backup_id": "backup"})

        # Compensation itself failed, but saga must still reach COMPENSATED
        assert context.state == SagaState.COMPENSATED
