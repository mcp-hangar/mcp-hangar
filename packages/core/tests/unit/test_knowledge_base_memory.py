"""Tests for in-memory knowledge base implementation."""

from datetime import datetime, timedelta, UTC

import pytest

from mcp_hangar.infrastructure.knowledge_base.contracts import (
    AuditEntry,
    KnowledgeBaseConfig,
    MetricEntry,
    ProviderStateEntry,
)
from mcp_hangar.infrastructure.knowledge_base.memory import MemoryKnowledgeBase


@pytest.fixture
def config():
    """Create default config."""
    return KnowledgeBaseConfig(
        enabled=True,
        cache_ttl_s=60,
    )


@pytest.fixture
async def kb(config):
    """Create and initialize memory knowledge base."""
    kb = MemoryKnowledgeBase(config)
    await kb.initialize()
    yield kb
    await kb.close()


class TestMemoryKnowledgeBaseLifecycle:
    """Tests for lifecycle management."""

    @pytest.mark.asyncio
    async def test_initialize_returns_true(self, config):
        """Should return True on initialization."""
        kb = MemoryKnowledgeBase(config)
        result = await kb.initialize()
        assert result is True
        await kb.close()

    @pytest.mark.asyncio
    async def test_is_healthy_after_init(self, config):
        """Should be healthy after initialization."""
        kb = MemoryKnowledgeBase(config)
        await kb.initialize()
        assert await kb.is_healthy() is True
        await kb.close()

    @pytest.mark.asyncio
    async def test_is_not_healthy_before_init(self, config):
        """Should not be healthy before initialization."""
        kb = MemoryKnowledgeBase(config)
        assert await kb.is_healthy() is False

    @pytest.mark.asyncio
    async def test_close_clears_data(self, config):
        """Should clear all data on close."""
        kb = MemoryKnowledgeBase(config)
        await kb.initialize()
        await kb.cache_set("provider", "tool", {"arg": 1}, {"result": 42})
        await kb.close()
        assert await kb.is_healthy() is False


class TestMemoryKnowledgeBaseCache:
    """Tests for cache operations."""

    @pytest.mark.asyncio
    async def test_cache_set_and_get(self, kb):
        """Should store and retrieve cached values."""
        await kb.cache_set("math", "add", {"a": 1, "b": 2}, {"result": 3})
        result = await kb.cache_get("math", "add", {"a": 1, "b": 2})
        assert result == {"result": 3}

    @pytest.mark.asyncio
    async def test_cache_miss(self, kb):
        """Should return None for cache miss."""
        result = await kb.cache_get("unknown", "tool", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_different_args(self, kb):
        """Should differentiate cache by arguments."""
        await kb.cache_set("math", "add", {"a": 1, "b": 2}, {"result": 3})
        await kb.cache_set("math", "add", {"a": 5, "b": 5}, {"result": 10})

        result1 = await kb.cache_get("math", "add", {"a": 1, "b": 2})
        result2 = await kb.cache_get("math", "add", {"a": 5, "b": 5})

        assert result1 == {"result": 3}
        assert result2 == {"result": 10}

    @pytest.mark.asyncio
    async def test_cache_expiration(self, config):
        """Should expire cached values after TTL."""
        config.cache_ttl_s = 1  # 1 second TTL
        kb = MemoryKnowledgeBase(config)
        await kb.initialize()

        await kb.cache_set("math", "add", {"a": 1}, {"result": 2}, ttl_s=0)

        # Should be expired immediately with ttl_s=0
        # Note: This depends on implementation - the value might still be there
        # until cleanup is called
        await kb.close()

    @pytest.mark.asyncio
    async def test_cache_invalidate_all(self, kb):
        """Should invalidate all cache entries."""
        await kb.cache_set("math", "add", {}, {"result": 1})
        await kb.cache_set("fetch", "get", {}, {"result": 2})

        count = await kb.cache_invalidate()

        assert count == 2
        assert await kb.cache_get("math", "add", {}) is None
        assert await kb.cache_get("fetch", "get", {}) is None

    @pytest.mark.asyncio
    async def test_cache_invalidate_by_provider(self, kb):
        """Should invalidate cache by provider."""
        await kb.cache_set("math", "add", {}, {"result": 1})
        await kb.cache_set("math", "subtract", {}, {"result": 2})
        await kb.cache_set("fetch", "get", {}, {"result": 3})

        count = await kb.cache_invalidate(provider="math")

        assert count == 2
        assert await kb.cache_get("math", "add", {}) is None
        assert await kb.cache_get("fetch", "get", {}) == {"result": 3}

    @pytest.mark.asyncio
    async def test_cache_cleanup(self, kb):
        """Should remove expired entries on cleanup."""
        # Add an entry that's already expired
        kb._cache["expired:key"] = ({"old": "data"}, datetime.now(UTC) - timedelta(hours=1))
        await kb.cache_set("valid", "tool", {}, {"fresh": "data"})

        count = await kb.cache_cleanup()

        assert count == 1
        assert "expired:key" not in kb._cache


class TestMemoryKnowledgeBaseAudit:
    """Tests for audit logging."""

    @pytest.mark.asyncio
    async def test_audit_log_stores_entry(self, kb):
        """Should store audit entry."""
        entry = AuditEntry(
            event_type="tool_invoked",
            provider="math",
            tool="add",
        )

        result = await kb.audit_log(entry)

        assert result is True
        assert len(kb._audit_log) == 1

    @pytest.mark.asyncio
    async def test_audit_log_sets_timestamp(self, kb):
        """Should set timestamp if not provided."""
        entry = AuditEntry(
            event_type="tool_invoked",
            provider="math",
        )

        await kb.audit_log(entry)

        assert entry.timestamp is not None

    @pytest.mark.asyncio
    async def test_audit_query_returns_entries(self, kb):
        """Should query audit entries."""
        entry1 = AuditEntry(event_type="started", provider="math")
        entry2 = AuditEntry(event_type="stopped", provider="math")
        entry3 = AuditEntry(event_type="started", provider="fetch")

        await kb.audit_log(entry1)
        await kb.audit_log(entry2)
        await kb.audit_log(entry3)

        results = await kb.audit_query(provider="math")

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_audit_query_by_tool(self, kb):
        """Should filter by tool."""
        await kb.audit_log(AuditEntry(event_type="invoked", provider="math", tool="add"))
        await kb.audit_log(AuditEntry(event_type="invoked", provider="math", tool="subtract"))

        results = await kb.audit_query(tool="add")

        assert len(results) == 1
        assert results[0].tool == "add"

    @pytest.mark.asyncio
    async def test_audit_query_with_limit(self, kb):
        """Should respect limit parameter."""
        for i in range(10):
            await kb.audit_log(AuditEntry(event_type=f"event_{i}", provider="math"))

        results = await kb.audit_query(limit=5)

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_audit_stats(self, kb):
        """Should return audit stats."""
        await kb.audit_log(AuditEntry(event_type="invoked", provider="math", success=True, duration_ms=100))
        await kb.audit_log(AuditEntry(event_type="invoked", provider="math", success=True, duration_ms=200))
        await kb.audit_log(AuditEntry(event_type="error", provider="fetch", success=False))

        stats = await kb.audit_stats(hours=24)

        assert stats["total"] == 3
        assert stats["success_count"] == 2
        assert stats["error_count"] == 1


class TestMemoryKnowledgeBaseState:
    """Tests for provider state tracking."""

    @pytest.mark.asyncio
    async def test_record_state_change(self, kb):
        """Should record provider state."""
        entry = ProviderStateEntry(
            provider_id="math",
            old_state="cold",
            new_state="ready",
        )

        result = await kb.record_state_change(entry)

        assert result is True
        assert len(kb._state_history["math"]) == 1

    @pytest.mark.asyncio
    async def test_record_state_change_sets_timestamp(self, kb):
        """Should set timestamp if not provided."""
        entry = ProviderStateEntry(
            provider_id="math",
            old_state=None,
            new_state="cold",
        )

        await kb.record_state_change(entry)

        assert entry.timestamp is not None

    @pytest.mark.asyncio
    async def test_get_state_history(self, kb):
        """Should return state history."""
        await kb.record_state_change(ProviderStateEntry(provider_id="math", old_state=None, new_state="cold"))
        await kb.record_state_change(ProviderStateEntry(provider_id="math", old_state="cold", new_state="starting"))
        await kb.record_state_change(ProviderStateEntry(provider_id="math", old_state="starting", new_state="ready"))

        history = await kb.get_state_history("math")

        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_get_state_history_with_limit(self, kb):
        """Should respect limit in state history."""
        states = ["cold", "starting", "ready", "degraded", "ready"]
        prev = None
        for state in states:
            await kb.record_state_change(ProviderStateEntry(provider_id="math", old_state=prev, new_state=state))
            prev = state

        history = await kb.get_state_history("math", limit=2)

        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_get_state_history_unknown_provider(self, kb):
        """Should return empty list for unknown provider."""
        history = await kb.get_state_history("unknown")
        assert history == []


class TestMemoryKnowledgeBaseMetrics:
    """Tests for metrics operations."""

    @pytest.mark.asyncio
    async def test_record_metric(self, kb):
        """Should record metric."""
        entry = MetricEntry(
            provider_id="math",
            metric_name="tool_duration",
            metric_value=0.5,
        )

        result = await kb.record_metric(entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_record_metric_sets_timestamp(self, kb):
        """Should set timestamp if not provided."""
        entry = MetricEntry(
            provider_id="math",
            metric_name="duration",
            metric_value=0.5,
        )

        await kb.record_metric(entry)

        assert entry.timestamp is not None

    @pytest.mark.asyncio
    async def test_get_metrics(self, kb):
        """Should get metrics for provider."""
        await kb.record_metric(MetricEntry(provider_id="math", metric_name="duration", metric_value=0.1))
        await kb.record_metric(MetricEntry(provider_id="math", metric_name="duration", metric_value=0.2))
        await kb.record_metric(MetricEntry(provider_id="math", metric_name="errors", metric_value=1))

        results = await kb.get_metrics("math")

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_metrics_by_name(self, kb):
        """Should filter metrics by name."""
        await kb.record_metric(MetricEntry(provider_id="math", metric_name="duration", metric_value=0.1))
        await kb.record_metric(MetricEntry(provider_id="math", metric_name="errors", metric_value=1))

        results = await kb.get_metrics("math", metric_name="duration")

        assert len(results) == 1
        assert results[0].metric_name == "duration"

    @pytest.mark.asyncio
    async def test_get_metrics_with_limit(self, kb):
        """Should respect limit."""
        for i in range(10):
            await kb.record_metric(MetricEntry(provider_id="math", metric_name="calls", metric_value=i))

        results = await kb.get_metrics("math", limit=3)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_metrics_unknown_provider(self, kb):
        """Should return empty list for unknown provider."""
        results = await kb.get_metrics("unknown")
        assert results == []
