"""The governance overlays a configuration puts in force, read as one set (#1431).

A configuration's governance is held in several overlays, each in its own
registry: the tool-access policies, group policies included, on the resolver;
the withdrawals, pins and digest-enforcement modes on the tool projection
registry; and the `header_exposure` blocks. A reload swaps each overlay whole,
under that registry's own lock (#1424), but one after another. So a decision
that reads two overlays could read one before its swap and the other after,
and apply a combination that neither file holds. For example, a reload that
moves a control from `tools.deny_list: [t]` to `tool_projection.withdrawn: [t]`:
the old withdrawals and the new policy together allow `t`.

So the swaps are counted as one generation. A commit holds `swapping` around
all of its overlay swaps, and the generation is odd while it does. A decision
that reads more than one overlay runs through `read_as_one_set`. It waits out a
swap in progress, and runs again when a swap began while it ran, so what it
returns was decided against one configuration's overlays.

No registry lock is taken here, and no lock is held while waiting: this
module's condition is never held while another lock is acquired, so this adds
no lock-order edge. What `read_as_one_set` runs must be safe to run more than
once, and it must not be called with a registry lock held: a swap waiting for
that lock would never end.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import threading
from typing import TypeVar

_T = TypeVar("_T")

#: Guards changes to `_generation`, and wakes the readers waiting out a swap.
_swap_done = threading.Condition(threading.Lock())
#: Odd while a swap is in progress; each swap adds two.
_generation = 0


def _settled() -> bool:
    return _generation % 2 == 0


@contextmanager
def swapping() -> Iterator[None]:
    """Hold across every overlay swap of one commit, so readers see all of them or none.

    One commit swaps at a time: a second one waits for the first to finish its
    swaps. Not reentrant.
    """
    global _generation
    with _swap_done:
        _swap_done.wait_for(_settled)
        _generation += 1
    try:
        yield
    finally:
        with _swap_done:
            _generation += 1
            _swap_done.notify_all()


def read_as_one_set(read: Callable[[], _T]) -> _T:
    """Run *read* against one configuration's overlays, never a mix of two.

    Args:
        read: A decision that reads more than one overlay. It may run more than
            once, so it must have no effect beyond its answer and its logging.

    Returns:
        What *read* returned on a run that no swap overlapped.
    """
    while True:
        before = _generation
        if before % 2 == 0:
            result = read()
            if _generation == before:
                return result
        with _swap_done:
            _swap_done.wait_for(_settled)
