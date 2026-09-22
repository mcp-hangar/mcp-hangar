"""How the aggregate reports who started a server and who waited for it (#1279).

Starting a cold server has two waiting mechanisms and, until this port existed,
neither was observable per caller. Ten concurrent calls to one cold server
produced one launch and nine waits, and a trace could not tell the caller doing
the work from the nine watching it: they all looked like slow calls.

The aggregate cannot answer that itself. It must not import a tracer -- ADR-029
keeps `domain/` free of the OpenTelemetry SDK, because a model that knows how it
is observed is a model that changes when the observer does. So it reports
through this narrow port, and an infrastructure adapter turns the report into
spans.

The default reports nothing and allocates nothing, so a deployment with tracing
off pays a context-manager entry and no more.

**Links, never a shared parent.** Many waiters share one cause, and ADR-029 s2
is explicit that a shared cause is a link: making the starter's span the parent
of nine other requests' work would put nine unrelated traces under one call.
Mapping the report to spans is the adapter's job, and that is the rule it
follows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, runtime_checkable


@runtime_checkable
class StartupObserver(Protocol):
    """Reports the role a caller plays in a shared startup.

    Both methods are context managers spanning the wait or the work. They are
    entered outside every aggregate lock: instrumentation that widened a lock
    would change the thing it is measuring.
    """

    def starting(self, mcp_server_id: str) -> AbstractContextManager[None]:
        """The caller that will perform startup, around the work itself."""
        ...

    def waiting(self, mcp_server_id: str) -> AbstractContextManager[None]:
        """A caller that will wait for someone else's startup, around the wait."""
        ...


class NullStartupObserver:
    """The default: reports nothing.

    Not a no-op for tidiness -- it is what keeps the port honest. A domain that
    only works when an observer is installed has an observer in its design, not
    beside it.
    """

    @contextmanager
    def starting(self, mcp_server_id: str) -> Iterator[None]:
        yield

    @contextmanager
    def waiting(self, mcp_server_id: str) -> Iterator[None]:
        yield


_observer: StartupObserver = NullStartupObserver()


def set_startup_observer(observer: StartupObserver | None) -> None:
    """Install the observer the aggregate reports to, or `None` to report nothing.

    Process-wide, like the other domain-level accessors here, because the
    aggregate is constructed in too many places to thread one through and a
    per-instance observer would leave the ones built elsewhere silent.
    """
    global _observer
    _observer = observer if observer is not None else NullStartupObserver()


def get_startup_observer() -> StartupObserver:
    """The installed observer, or the null one."""
    return _observer
