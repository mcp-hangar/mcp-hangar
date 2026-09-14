"""Unit-suite configuration: nothing a test starts may outlive it (#1389).

Two guards. A command a saga scheduled on the process-wide manager is cancelled
when the test that armed it ends: a recovery retry used to fire during a later
test, against a command bus that test had rebuilt, and log
`scheduled_command_failed` outside any test's captured output. And a thread
still running when the package ends fails it by name, as in
`tests/integration/conftest.py`.
"""

from collections.abc import Iterator
import sys
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


@pytest.fixture(autouse=True)
def no_scheduled_command_outlives_its_test() -> Iterator[None]:
    yield
    # Read from `sys.modules` so that a test which never built the manager does
    # not build one here: the manager subscribes itself to the global event bus.
    module = sys.modules.get("mcp_hangar.infrastructure.saga_manager")
    manager = getattr(module, "_saga_manager", None)
    if manager is not None:
        manager.cancel_all_scheduled_commands()


@pytest.fixture(scope="package", autouse=True)
def no_thread_outlives_the_unit_suite() -> Iterator[None]:
    before = set(threading.enumerate())
    yield
    fail_on_threads_started_since(before, suite="tests/unit", allowed=_ALLOWED_THREAD_PREFIXES)
