"""Integration-suite configuration.

A background thread still running when the suite ends is a leak (#1384). The
guard below fails the package with the name of each one and what it runs; the
check itself, and why it is package-scoped, are in `tests/_thread_guard.py`,
shared with `tests/unit/conftest.py`.
"""

from collections.abc import Iterator
import threading

import pytest

from tests._thread_guard import fail_on_threads_started_since

# Threads allowed to outlive the suite, by name prefix. Every entry says why it
# is harmless; a thread that fits none of them is a leak.
_ALLOWED_THREAD_PREFIXES = (
    # `approvals/service.py` keeps one process-wide pool for `_publish()`. Its
    # workers are non-daemon and idle on a queue; `concurrent.futures` wakes and
    # joins them at exit, before the interpreter finalizes, so they never race
    # stderr. Nothing stops them earlier because they belong to the process.
    "approval-publish_",
)


@pytest.fixture(scope="package", autouse=True)
def no_thread_outlives_the_integration_suite() -> Iterator[None]:
    before = set(threading.enumerate())
    yield
    fail_on_threads_started_since(before, suite="tests/integration", allowed=_ALLOWED_THREAD_PREFIXES)
