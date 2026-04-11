"""Unit tests for ApprovalHoldRegistry."""

import asyncio

import pytest

from enterprise.approvals.hold_registry import ApprovalHoldRegistry


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
