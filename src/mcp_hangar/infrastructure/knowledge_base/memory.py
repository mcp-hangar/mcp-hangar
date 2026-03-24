"""In-memory implementation of IKnowledgeBase for testing."""

from collections import defaultdict
from datetime import datetime, timedelta, UTC
import hashlib
import json
from typing import Any

from ...logging_config import get_logger
from .contracts import AuditEntry, IKnowledgeBase, KnowledgeBaseConfig, MetricEntry, ProviderStateEntry

logger = get_logger(__name__)


class MemoryKnowledgeBase(IKnowledgeBase):
    """In-memory implementation for testing."""

    def __init__(self, config: KnowledgeBaseConfig):
        self._config = config
        self._cache: dict[str, tuple[Any, datetime]] = {}  # key -> (result, expires_at)
        self._audit_log: list[AuditEntry] = []
        self._state_history: dict[str, list[ProviderStateEntry]] = defaultdict(list)
        self._metrics: dict[str, list[MetricEntry]] = defaultdict(list)
        self._initialized = False

    async def initialize(self) -> bool:
        self._initialized = True
        logger.info("memory_kb_initialized")
        return True

    async def close(self) -> None:
        self._cache.clear()
        self._audit_log.clear()
        self._state_history.clear()
        self._metrics.clear()
        self._initialized = False
        logger.info("memory_kb_closed")

    async def is_healthy(self) -> bool:
        return self._initialized

    def _hash_arguments(self, arguments: dict) -> str:
        serialized = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:32]

    def _cache_key(self, provider: str, tool: str, arguments: dict) -> str:
        return f"{provider}:{tool}:{self._hash_arguments(arguments)}"

    # === Cache Operations ===

    async def cache_get(self, provider: str, tool: str, arguments: dict) -> dict | None:
        key = self._cache_key(provider, tool, arguments)
        if key in self._cache:
            result, expires_at = self._cache[key]
            if expires_at > datetime.now(UTC):
                logger.debug("cache_hit", provider=provider, tool=tool)
                return result
            else:
                del self._cache[key]
        return None

    async def cache_set(
        self,
        provider: str,
        tool: str,
        arguments: dict,
        result: Any,
        ttl_s: int | None = None,
    ) -> bool:
        key = self._cache_key(provider, tool, arguments)
        ttl = ttl_s or self._config.cache_ttl_s
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
        self._cache[key] = (result, expires_at)
        return True

    async def cache_invalidate(self, provider: str | None = None, tool: str | None = None) -> int:
        if not provider and not tool:
            count = len(self._cache)
            self._cache.clear()
            return count

        prefix = f"{provider}:" if provider else ""
        keys_to_delete = [k for k in self._cache if k.startswith(prefix) and (not tool or f":{tool}:" in k)]
        for k in keys_to_delete:
            del self._cache[k]
        return len(keys_to_delete)

    async def cache_cleanup(self) -> int:
        now = datetime.now(UTC)
        expired = [k for k, (_, exp) in self._cache.items() if exp < now]
        for k in expired:
            del self._cache[k]
        logger.info("cache_cleanup", deleted=len(expired))
        return len(expired)

    # === Audit Operations ===

    async def audit_log(self, entry: AuditEntry) -> bool:
        entry.timestamp = entry.timestamp or datetime.now(UTC)
        self._audit_log.append(entry)
        return True

    async def audit_query(
        self,
        provider: str | None = None,
        tool: str | None = None,
        success: bool | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        results = []
        for entry in reversed(self._audit_log):
            if provider and entry.provider != provider:
                continue
            if tool and entry.tool != tool:
                continue
            if success is not None and entry.success != success:
                continue
            if since and entry.timestamp and entry.timestamp < since:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    async def audit_stats(self, hours: int = 24) -> dict:
        since = datetime.now(UTC) - timedelta(hours=hours)
        recent = [e for e in self._audit_log if e.timestamp and e.timestamp >= since]

        durations = [e.duration_ms for e in recent if e.duration_ms is not None]

        return {
            "total": len(recent),
            "success_count": sum(1 for e in recent if e.success),
            "error_count": sum(1 for e in recent if not e.success),
            "providers": len(set(e.provider for e in recent if e.provider)),
            "tools": len(set(e.tool for e in recent if e.tool)),
            "avg_duration_ms": sum(durations) / len(durations) if durations else None,
        }

    # === Provider State Operations ===

    async def record_state_change(self, entry: ProviderStateEntry) -> bool:
        entry.timestamp = entry.timestamp or datetime.now(UTC)
        self._state_history[entry.provider_id].append(entry)
        return True

    async def get_state_history(self, provider_id: str, limit: int = 100) -> list[ProviderStateEntry]:
        history = self._state_history.get(provider_id, [])
        return list(reversed(history[-limit:]))

    # === Metrics Operations ===

    async def record_metric(self, entry: MetricEntry) -> bool:
        entry.timestamp = entry.timestamp or datetime.now(UTC)
        self._metrics[entry.provider_id].append(entry)
        return True

    async def get_metrics(
        self,
        provider_id: str,
        metric_name: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[MetricEntry]:
        metrics = self._metrics.get(provider_id, [])

        results = []
        for m in reversed(metrics):
            if metric_name and m.metric_name != metric_name:
                continue
            if since and m.timestamp and m.timestamp < since:
                continue
            results.append(m)
            if len(results) >= limit:
                break
        return results
