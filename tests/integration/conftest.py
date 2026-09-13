"""Integration-suite configuration.

A background thread still running when the suite ends is a leak, and a costly
one: a daemon thread that writes to stderr while the interpreter finalizes can
abort the process after every test has passed -- `Fatal Python error:
_enter_buffered_busy`, exit 134, and no test to blame (#1384). The guard below
turns that into an error that names the thread and what it runs, so the leak is
found at its owner instead of guessed at from a core dump.
"""

from collections.abc import Iterator
import threading
import time

import pytest

# Threads allowed to outlive the suite, by name prefix. Every entry says why it
# is harmless; a thread that fits none of them is a leak.
_ALLOWED_THREAD_PREFIXES = (
    # `approvals/service.py` keeps one process-wide pool for `_publish()`. Its
    # workers are non-daemon and idle on a queue; `concurrent.futures` wakes and
    # joins them at exit, before the interpreter finalizes, so they never race
    # stderr. Nothing stops them earlier because they belong to the process.
    "approval-publish_",
)

# How long a thread that was told to stop gets to finish before it counts as
# leaked. A closed HTTP client's GET-stream thread, for one, notices only after
# the reconnect backoff it is sleeping through.
_GRACE_S = 5.0


def _describe(thread: threading.Thread) -> str:
    # `threading.Timer` overrides `run()` and keeps its callable in `function`.
    target = getattr(thread, "_target", None) or getattr(thread, "function", None)
    if target is None:
        where = type(thread).__qualname__
    else:
        where = f"{getattr(target, '__module__', '?')}.{getattr(target, '__qualname__', repr(target))}"
    return f"{thread.name!r} (daemon={thread.daemon}, target={where})"


# Package scope, not session: `decision-coverage` runs `tests/unit` and this
# package in ONE session, and a session-wide check blamed this package for every
# thread the unit tests had left behind. Only threads started while this package
# ran are its own. The snapshot holds Thread objects rather than idents because
# CPython reuses the ident of a dead thread, which would hide a leak behind it.
@pytest.fixture(scope="package", autouse=True)
def no_thread_outlives_the_integration_suite() -> Iterator[None]:
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + _GRACE_S
    leaked = []
    for thread in threading.enumerate():
        if thread in before or thread.name.startswith(_ALLOWED_THREAD_PREFIXES):
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            leaked.append(thread)
    if leaked:
        names = "\n".join(f"  {_describe(thread)}" for thread in leaked)
        pytest.fail(
            f"{len(leaked)} background thread(s) started by tests/integration are still running:\n{names}\n"
            "Stop each one in the test or fixture that started it, or add it to "
            "_ALLOWED_THREAD_PREFIXES in tests/integration/conftest.py with the reason it is harmless.",
            pytrace=False,
        )
