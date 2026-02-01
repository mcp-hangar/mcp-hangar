"""Response truncation infrastructure.

Provides caching and truncation management for batch responses
that exceed Claude's context limits.

Components:
- MemoryResponseCache: Thread-safe LRU cache with TTL
- RedisResponseCache: Redis-backed cache for distributed deployments
- TruncationManager: Orchestrates truncation and caching
"""

from .manager import TruncationManager
from .memory_cache import MemoryResponseCache

__all__ = [
    "MemoryResponseCache",
    "TruncationManager",
]

# RedisResponseCache is imported on demand to avoid requiring redis dependency
