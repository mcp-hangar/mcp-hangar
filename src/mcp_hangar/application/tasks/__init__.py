"""Application-layer wiring for MCP experimental task support.

Hosts the ownership-governed ``TaskStore`` that binds each task to its owning
tenant/principal and fail-closed-authorizes ``tasks/*`` access.
"""

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore

__all__ = ["GovernedTaskStore"]
