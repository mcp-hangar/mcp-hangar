"""An abandoned hold is cancelled in the record at once, so no resolver anywhere can approve it (#2272).

The gateway knew a held call was abandoned only in its in-process hold
registry. A resolver in another process -- the ``approvals`` CLI, or REST on
another replica -- could not see it, moved the record from pending to approved
and reported success for a call that never ran. The record turned ``cancelled``
only when the gateway's approval window ended.

Two gate services share one SQLite store here, as a gateway and the CLI share
it on a host: the first holds the call, the second resolves it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.approvals import service as service_mod
from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalResult, ApprovalState
from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
from mcp_hangar.approvals.service import ABANDONED_REASON, CANCELLED_REASON, ApprovalGateService
from mcp_hangar.domain.events import ToolApprovalCancelled, ToolApprovalGranted
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig

POLICY = ToolAccessPolicy(approval_list=("send_message",), approval_timeout_seconds=30)


def _repository(db: Path) -> SqliteApprovalRepository:
    return SqliteApprovalRepository(Database(DatabaseConfig(path=str(db))))


def _service(db: Path, repository: Any = None) -> ApprovalGateService:
    return ApprovalGateService(
        repository or _repository(db), ApprovalHoldRegistry(), MagicMock(), NoOpApprovalDelivery()
    )


def _published(service: ApprovalGateService) -> list[Any]:
    return [c.args[0] for c in service._event_bus.publish.call_args_list]  # type: ignore[attr-defined]


async def _hold(gateway: ApprovalGateService, abandoned: threading.Event) -> tuple[asyncio.Task[ApprovalResult], str]:
    """Start a held call on *gateway*; return the hold and its approval id once it is pending."""
    # The store's first use creates its schema; done here, it never races the gate's own first write.
    assert await gateway._repository.list_by_state(ApprovalState.PENDING) == []
    hold = asyncio.create_task(
        gateway.check("send_message", {"text": "hi"}, POLICY, "corr-1", mcp_server_id="mail", abandoned=abandoned)
    )
    for _ in range(200):
        pending = await gateway._repository.list_by_state(ApprovalState.PENDING)
        if pending:
            return hold, pending[0].approval_id
        await asyncio.sleep(0.01)
    raise AssertionError("the call was never held")


async def _until_not_pending(service: ApprovalGateService, approval_id: str) -> None:
    for _ in range(200):
        record = await service._repository.get(approval_id)
        if record is not None and record.state is not ApprovalState.PENDING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the abandoned hold stayed pending")


async def test_approving_an_abandoned_hold_from_another_process_is_refused(tmp_path: Path) -> None:
    gateway, cli = _service(tmp_path / "a.db"), _service(tmp_path / "a.db")
    abandoned = threading.Event()
    hold, approval_id = await _hold(gateway, abandoned)

    started = time.monotonic()
    abandoned.set()
    await _until_not_pending(cli, approval_id)
    # Seen from the abandonment itself, not from the next poll of the record.
    assert time.monotonic() - started < service_mod.SHARED_POLL_INTERVAL_S

    assert await cli.resolve(approval_id, True, "local-approver") is False
    result = await asyncio.wait_for(hold, timeout=5)

    assert result.error_code == "CancellationError", result
    record = await cli._repository.get(approval_id)
    assert record is not None
    assert (record.state, record.decided_by, record.reason) == (ApprovalState.CANCELLED, None, ABANDONED_REASON)
    assert await cli._repository.list_by_state(ApprovalState.PENDING) == []
    events = _published(gateway)
    assert not [e for e in events if isinstance(e, ToolApprovalGranted)], events
    (cancelled,) = [e for e in events if isinstance(e, ToolApprovalCancelled)]
    assert (cancelled.attempted_by, cancelled.reason) == ("", ABANDONED_REASON)


async def test_a_denial_after_the_abandonment_is_refused_too(tmp_path: Path) -> None:
    gateway, cli = _service(tmp_path / "a.db"), _service(tmp_path / "a.db")
    abandoned = threading.Event()
    hold, approval_id = await _hold(gateway, abandoned)

    abandoned.set()
    await _until_not_pending(cli, approval_id)

    assert await cli.resolve(approval_id, False, "local-approver", "no") is False
    assert (await asyncio.wait_for(hold, timeout=5)).error_code == "CancellationError"
    record = await cli._repository.get(approval_id)
    assert record is not None and record.state is ApprovalState.CANCELLED


async def test_approving_a_live_hold_from_another_process_grants_it_once(tmp_path: Path) -> None:
    gateway, cli = _service(tmp_path / "a.db"), _service(tmp_path / "a.db")
    hold, approval_id = await _hold(gateway, threading.Event())

    with patch.object(service_mod, "SHARED_POLL_INTERVAL_S", 0.1):
        assert await cli.resolve(approval_id, True, "local-approver") is True
        assert await cli.resolve(approval_id, True, "local-approver") is False
        result = await asyncio.wait_for(hold, timeout=5)

    assert result.approved, result
    record = await gateway._repository.get(approval_id)
    assert record is not None
    assert (record.state, record.decided_by) == (ApprovalState.APPROVED, "local-approver")
    granted = [e for e in _published(gateway) if isinstance(e, ToolApprovalGranted)]
    assert len(granted) == 1, granted


async def test_an_approval_that_lands_just_before_the_abandonment_is_recorded_cancelled(tmp_path: Path) -> None:
    """The residual race: the cross-process approval wins the record before the cancel does."""
    gateway, cli = _service(tmp_path / "a.db"), _service(tmp_path / "a.db")
    abandoned = threading.Event()
    with patch.object(service_mod, "SHARED_POLL_INTERVAL_S", 30.0):
        hold, approval_id = await _hold(gateway, abandoned)
        assert await cli.resolve(approval_id, True, "local-approver") is True
        abandoned.set()  # before the gateway has read the approval from the record
        result = await asyncio.wait_for(hold, timeout=5)

    assert result.error_code == "CancellationError", result
    record = await gateway._repository.get(approval_id)
    assert record is not None
    assert (record.state, record.decided_by, record.reason) == (
        ApprovalState.CANCELLED,
        "local-approver",
        CANCELLED_REASON,
    )
    events = _published(gateway)
    assert not [e for e in events if isinstance(e, ToolApprovalGranted)], events
    (cancelled,) = [e for e in events if isinstance(e, ToolApprovalCancelled)]
    assert cancelled.attempted_by == "local-approver"


async def test_a_denial_that_lands_just_before_the_abandonment_stays_a_denial(tmp_path: Path) -> None:
    gateway, cli = _service(tmp_path / "a.db"), _service(tmp_path / "a.db")
    abandoned = threading.Event()
    with patch.object(service_mod, "SHARED_POLL_INTERVAL_S", 30.0):
        hold, approval_id = await _hold(gateway, abandoned)
        assert await cli.resolve(approval_id, False, "local-approver", "no") is True
        abandoned.set()
        result = await asyncio.wait_for(hold, timeout=5)

    assert (result.error_code, result.reason) == ("approval_denied", "no"), result
    record = await gateway._repository.get(approval_id)
    assert record is not None and record.state is ApprovalState.DENIED


class _CancelFails:
    """A store whose cancel write fails, or finds the record already expired by someone else."""

    def __init__(self, inner: SqliteApprovalRepository, *, raises: bool) -> None:
        self._inner = inner
        self._raises = raises

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def transition(self, approval_id, from_states, state, decided_by, decided_at, reason) -> bool:
        if state is ApprovalState.CANCELLED:
            if self._raises:
                raise OSError("disk I/O error")
            await self._inner.transition(approval_id, from_states, ApprovalState.EXPIRED, None, decided_at, None)
            return False
        return await self._inner.transition(approval_id, from_states, state, decided_by, decided_at, reason)


@pytest.mark.parametrize("raises", [True, False], ids=["write-fails", "already-expired"])
async def test_a_cancel_that_moves_nothing_leaves_the_hold_to_its_window(tmp_path: Path, raises: bool) -> None:
    """The cancel is tried once: a record it could not move ends the hold the way it did before #2272."""
    repository = _CancelFails(_repository(tmp_path / "a.db"), raises=raises)
    gateway = ApprovalGateService(
        repository, ApprovalHoldRegistry(), MagicMock(), NoOpApprovalDelivery(), wait_limit_seconds=1
    )
    abandoned = threading.Event()
    hold, approval_id = await _hold(gateway, abandoned)

    abandoned.set()
    result = await asyncio.wait_for(hold, timeout=5)

    assert result.error_code == "approval_timeout", result
    record = await repository.get(approval_id)
    assert record is not None and record.state is ApprovalState.EXPIRED
