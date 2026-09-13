"""The check behind the thread guard in each suite's `conftest.py`.

A background thread still running when a suite ends is a leak, and a costly
one: a daemon thread that writes to stderr while the interpreter finalizes can
abort the process after every test has passed -- `Fatal Python error:
_enter_buffered_busy`, exit 134, and no test to blame (#1384). The check turns
that into an error that names the thread and what it runs, so the leak is found
at its owner instead of guessed at from a core dump.

Each suite calls it from a package-scoped fixture, not a session one:
`decision-coverage` runs `tests/unit` and `tests/integration` in ONE session,
and a session-wide check blamed the suite that ran last for every thread the
other had left behind. Only threads started while a package ran are its own. The
snapshot holds Thread objects rather than idents because CPython reuses the
ident of a dead thread, which would hide a leak behind it.
"""

import threading
import time

import pytest

# How long a thread that was told to stop gets to finish before it counts as
# leaked. A closed HTTP client's GET-stream thread, for one, notices only after
# the reconnect backoff it is sleeping through.
GRACE_S = 5.0


def _describe(thread: threading.Thread) -> str:
    # `threading.Timer` overrides `run()` and keeps its callable in `function`.
    target = getattr(thread, "_target", None) or getattr(thread, "function", None)
    if target is None:
        where = type(thread).__qualname__
    else:
        where = f"{getattr(target, '__module__', '?')}.{getattr(target, '__qualname__', repr(target))}"
    return f"{thread.name!r} (daemon={thread.daemon}, target={where})"


def fail_on_threads_started_since(before: set[threading.Thread], *, suite: str, allowed: tuple[str, ...]) -> None:
    """Fail naming every thread not in `before` that is still running after the grace period.

    Args:
        before: The threads that were running when the package started.
        suite: The package, as a path from the repository root.
        allowed: Name prefixes of threads that may outlive it.
    """
    deadline = time.monotonic() + GRACE_S
    leaked = []
    for thread in threading.enumerate():
        if thread in before or thread.name.startswith(allowed):
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            leaked.append(thread)
    if leaked:
        names = "\n".join(f"  {_describe(thread)}" for thread in leaked)
        pytest.fail(
            f"{len(leaked)} background thread(s) started by {suite} are still running:\n{names}\n"
            "Stop each one in the test or fixture that started it, or add it to "
            f"_ALLOWED_THREAD_PREFIXES in {suite}/conftest.py with the reason it is harmless.",
            pytrace=False,
        )
