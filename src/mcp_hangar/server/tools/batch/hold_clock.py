"""The time a call spends held for approval, for a caller that waits on the call from outside (#2271).

The executor leaves the approval hold out of a call's budget: the call's own
clock stops while a human decides, and the upstream gets the budget it had when
the hold began. A caller that bounds its own wait around the executor -- the
Python facade's ``invoke`` -- has to leave the hold out the same way, or it
gives up on a call the executor still runs. It watches a :class:`HoldClock`
while it makes the call: :func:`held_for_approval` runs the hold on the clock
of the caller that is watching, and on none when nobody is. The clock reaches
the batch's worker threads through the context they copy.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class HoldClock:
    """How long the calls made under it have been held for approval, an open hold counted to now."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open = 0
        self._opened_at = 0.0
        self._held = 0.0

    def held_s(self) -> float:
        """Seconds held so far. Two holds at once count once: the clock stops while any call is held."""
        with self._lock:
            open_s = time.perf_counter() - self._opened_at if self._open else 0.0
            return self._held + open_s

    def holding(self) -> bool:
        """Whether a call is held right now."""
        with self._lock:
            return self._open > 0

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Count the time inside as held."""
        with self._lock:
            if not self._open:
                self._opened_at = time.perf_counter()
            self._open += 1
        try:
            yield
        finally:
            with self._lock:
                self._open -= 1
                if not self._open:
                    self._held += time.perf_counter() - self._opened_at


_watching: ContextVar[HoldClock | None] = ContextVar("hangar_hold_clock", default=None)


@contextmanager
def watching(clock: HoldClock) -> Iterator[None]:
    """Run the holds of the calls made inside on *clock*."""
    token = _watching.set(clock)
    try:
        yield
    finally:
        _watching.reset(token)


@contextmanager
def held_for_approval() -> Iterator[None]:
    """Count the time inside as held on the clock of the caller watching, if one is."""
    clock = _watching.get()
    if clock is None:
        yield
        return
    with clock.hold():
        yield
