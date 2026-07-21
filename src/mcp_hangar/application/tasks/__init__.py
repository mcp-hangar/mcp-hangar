"""Application-layer wiring for MCP experimental task support.

Hosts the ownership-governed ``TaskStore`` that binds each task to its owning
tenant/principal and fail-closed-authorizes ``tasks/*`` access.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore

__all__ = ["GovernedTaskStore"]


def __getattr__(name: str):
    # Lazy (PEP 562): ``governed_task_store`` imports the SDK v1
    # ``mcp.shared.experimental.tasks`` store, which SDK v2 removed. Importing it
    # lazily keeps ``import mcp_hangar.application.tasks`` safe on v2 — the module
    # is only loaded when GovernedTaskStore is actually accessed, which happens
    # only behind the HAS_EXPERIMENTAL_TASKS guard in the factory (dormant on v2).
    if name == "GovernedTaskStore":
        from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore

        return GovernedTaskStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
