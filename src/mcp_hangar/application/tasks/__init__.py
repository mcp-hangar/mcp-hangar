"""Application-layer wiring for MCP task relay governance (ADR-014).

Hosts the v2-native :class:`GovernedTaskStore` -- a synchronous in-memory
governance ledger that binds each relayed task to its owning tenant/principal
and pinned tool digest, keeps an upstream-truth snapshot, and fail-closed-
authorizes ``tasks/*`` access. It holds governance metadata ONLY (never results
or execution state) and has no dependence on the removed SDK v1 experimental
task store, so it imports and constructs standalone on SDK v2.
"""

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore

__all__ = ["GovernedTaskStore"]
