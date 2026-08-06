"""A decision made on one instance releases the call held on another.

The hold is a `threading.Event` in one process. A call held on instance A while
the approver's `POST /approvals/{id}/resolve` lands on instance B would sit on
that event until it timed out and then fail closed -- so the approver saw
success, the caller saw a denial, and the record said approved. The record and
the outcome disagreeing is worse than plain unavailability, and it was silent
(#778).

The record is what the two instances share once a storage backend is selected,
so the wait watches both: the local hold for the common case, the record for the
one that used to be impossible.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalState


class _Repository:
    """Stands in for storage shared between instances."""

    def __init__(self, state: ApprovalState = ApprovalState.PENDING) -> None:
        self.state = state
        self.reads = 0

    async def get(self, approval_id: str) -> Any:
        self.reads += 1
        return SimpleNamespace(state=self.state, decided_by="ops@example.com", decided_at=None)


class _BrokenRepository:
    async def get(self, approval_id: str) -> Any:
        raise RuntimeError("storage is unreachable")


def _service(repository: Any, registry: ApprovalHoldRegistry) -> Any:
    """An ApprovalGateService with only the two collaborators the wait uses."""
    from mcp_hangar.approvals.service import ApprovalGateService

    service = ApprovalGateService.__new__(ApprovalGateService)
    service._repository = repository
    service._hold_registry = registry
    return service


@pytest.mark.asyncio
class TestADecisionOnAnotherInstanceIsSeen:
    async def test_an_approval_written_to_shared_storage_releases_the_call(self) -> None:
        registry = ApprovalHoldRegistry()
        await registry.register("a-1")
        repository = _Repository(ApprovalState.APPROVED)

        decision = await _service(repository, registry)._wait_for_decision("a-1", timeout_seconds=5)

        assert decision is True

    async def test_a_denial_written_to_shared_storage_refuses_the_call(self) -> None:
        registry = ApprovalHoldRegistry()
        await registry.register("a-2")
        repository = _Repository(ApprovalState.DENIED)

        decision = await _service(repository, registry)._wait_for_decision("a-2", timeout_seconds=5)

        assert decision is False

    async def test_a_decision_arriving_late_is_still_seen(self) -> None:
        # The realistic shape: the call is already waiting when the approver
        # acts on the other instance.
        registry = ApprovalHoldRegistry()
        await registry.register("a-3")
        repository = _Repository(ApprovalState.PENDING)

        async def decide_later() -> None:
            await asyncio.sleep(0.2)
            repository.state = ApprovalState.APPROVED

        asyncio.create_task(decide_later())
        decision = await _service(repository, registry)._wait_for_decision("a-3", timeout_seconds=8)

        assert decision is True


@pytest.mark.asyncio
class TestTheLocalPathIsStillTheFastOne:
    async def test_a_local_resolution_does_not_wait_for_a_poll(self) -> None:
        # Same-instance approval is the common case and must stay immediate --
        # the shared read exists for the other case, not instead of this one.
        registry = ApprovalHoldRegistry()
        await registry.register("a-4")
        repository = _Repository(ApprovalState.PENDING)

        async def resolve_now() -> None:
            await asyncio.sleep(0.01)
            await registry.resolve("a-4", approved=True)

        asyncio.create_task(resolve_now())
        loop = asyncio.get_running_loop()
        started = loop.time()
        decision = await _service(repository, registry)._wait_for_decision("a-4", timeout_seconds=8)

        assert decision is True
        assert loop.time() - started < 1.0, "a local decision waited for the shared poll"

    async def test_the_record_is_not_read_when_the_local_hold_answers(self) -> None:
        registry = ApprovalHoldRegistry()
        await registry.register("a-5")
        repository = _Repository(ApprovalState.PENDING)
        await registry.resolve("a-5", approved=True)

        await _service(repository, registry)._wait_for_decision("a-5", timeout_seconds=5)

        assert repository.reads == 0


@pytest.mark.asyncio
class TestNothingHereCanDecideByAccident:
    async def test_a_pending_record_does_not_release_the_call(self) -> None:
        registry = ApprovalHoldRegistry()
        await registry.register("a-6")

        decision = await _service(_Repository(ApprovalState.PENDING), registry)._wait_for_decision(
            "a-6", timeout_seconds=1
        )

        assert decision is None, "pending is not a decision; the gate must fail closed on timeout"

    async def test_unreadable_storage_does_not_decide_the_call(self) -> None:
        # A storage hiccup must not turn a pending approval into a refusal. The
        # deadline still applies, so the worst case is the old behaviour.
        registry = ApprovalHoldRegistry()
        await registry.register("a-7")

        decision = await _service(_BrokenRepository(), registry)._wait_for_decision("a-7", timeout_seconds=1)

        assert decision is None

    async def test_the_hold_is_released_afterwards(self) -> None:
        # Held entries are per call; leaving them behind is a slow leak in the
        # one component that runs for the lifetime of the process.
        registry = ApprovalHoldRegistry()
        await registry.register("a-8")

        await _service(_Repository(ApprovalState.APPROVED), registry)._wait_for_decision("a-8", timeout_seconds=5)

        assert registry._holds == {}
