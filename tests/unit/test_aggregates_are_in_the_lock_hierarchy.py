"""Every aggregate's lock must be registered in the global ordering.

`TrackedLock` exists so that acquiring locks out of order raises
`LockOrderViolation` instead of deadlocking. An aggregate holding a plain
`threading.RLock` is invisible to that check: it still deadlocks, it just does
so silently, at runtime, under load.

Two ways that had happened:

* `McpServer._create_lock` and `McpServerGroup._create_lock` wrapped the import
  in `try/except ImportError` and fell back to a bare `RLock`. The except branch
  could never run -- the module ships in the package -- so it guarded against
  nothing while advertising a silent downgrade.
* `EventSourcedMcpServer.__init__` did not go through `_create_lock` at all. It
  assigned `threading.RLock()` directly, unconditionally, so every
  event-sourced aggregate was outside the hierarchy. That one was live, not
  hypothetical, and typing `_create_lock` is what surfaced it.

So this asserts the lock each aggregate actually ends up holding, rather than
the code path that creates it.

The `EventSourcedMcpServer` case is gone from the list below because the class
itself is gone: it was an unwired artifact of the enterprise migration, never
constructed in `src/` in four months, and was deleted along with its repository.
The bug it recorded is kept here in prose because the LESSON outlives the class
-- the next aggregate that assigns `threading.RLock()` directly will be outside
the hierarchy in exactly the same way.
"""

from __future__ import annotations

from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.lock_hierarchy import LockLevel, TrackedLock


class TestAggregateLocksAreTracked:
    def test_mcp_server(self):
        server = McpServer(mcp_server_id="demo", mode="subprocess", command=["true"])
        assert isinstance(server._lock, TrackedLock)
        assert server._lock.level == LockLevel.PROVIDER

    def test_mcp_server_group(self):
        group = McpServerGroup(group_id="pool")
        assert isinstance(group._lock, TrackedLock)
        assert group._lock.level == LockLevel.PROVIDER_GROUP

    def test_repository(self):
        repository = InMemoryMcpServerRepository()
        assert isinstance(repository._lock, TrackedLock)
        assert repository._lock.level == LockLevel.REPOSITORY


class TestTheLevelsStayOrdered:
    """The hierarchy is only meaningful if aggregates sort before what they call.

    An aggregate holds its lock while dispatching to a client, so PROVIDER must
    sort before the transports; inverting these would make every legitimate call
    path a violation.
    """

    def test_aggregates_come_before_persistence_and_io(self):
        assert LockLevel.PROVIDER < LockLevel.PROVIDER_GROUP < LockLevel.REPOSITORY
        assert LockLevel.REPOSITORY < LockLevel.STDIO_CLIENT
        assert LockLevel.REPOSITORY < LockLevel.HTTP_CLIENT


class TestTheModuleIsSharedKernel:
    """It moved out of `infrastructure/` because three layers speak it.

    The import contract now lists it on the shared-kernel line, which is what
    lets the domain import it directly instead of through a `try/except` that
    could never fire.
    """

    def test_it_imports_nothing_above_the_shared_kernel(self):
        import pathlib

        import mcp_hangar.lock_hierarchy as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("from .domain", "from .application", "from .infrastructure", "from .server"):
            assert forbidden not in source, f"lock_hierarchy reaches up into {forbidden}"

    def test_the_infrastructure_re_export_still_works(self):
        """`from mcp_hangar.infrastructure import TrackedLock` is the documented surface."""
        from mcp_hangar.infrastructure import LockLevel as ReExportedLevel, TrackedLock as ReExported

        assert ReExported is TrackedLock
        assert ReExportedLevel is LockLevel
