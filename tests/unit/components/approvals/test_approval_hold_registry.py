"""Unit tests for ApprovalHoldRegistry."""

import asyncio
import threading
import time

import pytest

from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry


@pytest.fixture
def registry():
    return ApprovalHoldRegistry()


class TestApprovalHoldRegistry:
    async def test_register_wait_resolve_approve(self, registry):
        """Register -> wait -> resolve approve -> returns True."""
        await registry.register("id-001")

        async def approve_later():
            await asyncio.sleep(0.05)
            await registry.resolve("id-001", approved=True)

        asyncio.create_task(approve_later())
        result = await registry.wait("id-001", timeout_seconds=5)
        assert result is True

    async def test_register_wait_resolve_deny(self, registry):
        """Register -> wait -> resolve deny -> returns False."""
        await registry.register("id-001")

        async def deny_later():
            await asyncio.sleep(0.05)
            await registry.resolve("id-001", approved=False)

        asyncio.create_task(deny_later())
        result = await registry.wait("id-001", timeout_seconds=5)
        assert result is False

    async def test_wait_timeout_returns_none(self, registry):
        """Wait with very short timeout -> returns None."""
        await registry.register("id-001")
        result = await registry.wait("id-001", timeout_seconds=0)
        assert result is None

    async def test_resolve_unknown_id_returns_false(self, registry):
        """Resolve on unknown approval_id returns False."""
        result = await registry.resolve("nonexistent", approved=True)
        assert result is False

    async def test_wait_unknown_id_returns_none(self, registry):
        """Wait on unknown approval_id returns None."""
        result = await registry.wait("nonexistent", timeout_seconds=1)
        assert result is None

    async def test_hold_cleaned_up_after_wait(self, registry):
        """After wait completes, the hold entry is removed."""
        await registry.register("id-001")

        async def resolve_soon():
            await asyncio.sleep(0.05)
            await registry.resolve("id-001", approved=True)

        asyncio.create_task(resolve_soon())
        await registry.wait("id-001", timeout_seconds=5)

        # Second resolve should return False (entry cleaned up)
        result = await registry.resolve("id-001", approved=True)
        assert result is False

    async def test_concurrent_waiters(self, registry):
        """Multiple concurrent waits on the same approval_id."""
        await registry.register("id-001")

        async def resolve_later():
            await asyncio.sleep(0.05)
            await registry.resolve("id-001", approved=True)

        asyncio.create_task(resolve_later())

        result = await registry.wait("id-001", timeout_seconds=5)
        assert result is True


class TestASliceThatWatchesForAbandonment:
    """`until_abandoned` ends a slice when the caller gives up, and only then (#2272)."""

    async def test_it_ends_undecided_once_the_caller_abandons(self, registry):
        abandoned = threading.Event()
        await registry.register("id-001", abandoned=abandoned)
        asyncio.get_running_loop().call_later(0.05, abandoned.set)

        started = time.monotonic()
        assert await registry.wait_slice("id-001", 5, until_abandoned=True) is None
        assert time.monotonic() - started < 1

    async def test_it_ends_at_once_for_a_call_already_abandoned(self, registry):
        abandoned = threading.Event()
        abandoned.set()
        await registry.register("id-001", abandoned=abandoned)

        assert await registry.wait_slice("id-001", 5, until_abandoned=True) is None

    async def test_a_decision_still_wakes_it(self, registry):
        await registry.register("id-001", abandoned=threading.Event())
        asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(registry.resolve("id-001", True)))

        assert await registry.wait_slice("id-001", 5, until_abandoned=True) is True

    async def test_it_elapses_like_any_slice_when_nothing_happens(self, registry):
        await registry.register("id-001", abandoned=threading.Event())

        assert await registry.wait_slice("id-001", 0.1, until_abandoned=True) is None

    async def test_without_it_an_abandonment_does_not_end_the_slice(self, registry):
        abandoned = threading.Event()
        abandoned.set()
        await registry.register("id-001", abandoned=abandoned)

        started = time.monotonic()
        assert await registry.wait_slice("id-001", 0.2) is None
        assert time.monotonic() - started >= 0.2
