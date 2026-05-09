"""Tests for thread-local event loop reuse in the approval gate path."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from mcp_hangar.server.tools.batch import executor as exec_mod


def _reset_thread_local():
    if hasattr(exec_mod._approval_loop_local, "loop"):
        delattr(exec_mod._approval_loop_local, "loop")


def test_approval_loop_reused_within_thread(monkeypatch):
    """The same thread reuses the same event loop across approval calls."""
    created: list[asyncio.AbstractEventLoop] = []
    original_new_loop = asyncio.new_event_loop

    def tracking_new_loop():
        loop = original_new_loop()
        created.append(loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", tracking_new_loop)
    _reset_thread_local()

    loop_a = exec_mod._get_approval_loop()
    loop_b = exec_mod._get_approval_loop()
    loop_c = exec_mod._get_approval_loop()

    assert loop_a is loop_b is loop_c
    assert len(created) == 1


def test_approval_loop_recreated_after_close():
    """If a thread's loop is externally closed, _get_approval_loop creates a new one."""
    _reset_thread_local()

    loop1 = exec_mod._get_approval_loop()
    loop1.close()

    loop2 = exec_mod._get_approval_loop()
    assert loop2 is not loop1
    assert not loop2.is_closed()


def test_approval_loops_are_thread_local():
    """Each worker thread gets its own loop."""
    _reset_thread_local()

    import threading as _threading

    barrier = _threading.Barrier(3)
    loops_seen: list[asyncio.AbstractEventLoop] = []
    lock = _threading.Lock()

    def get_loop(_):
        loop = exec_mod._get_approval_loop()
        with lock:
            loops_seen.append(loop)
        barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(get_loop, range(3)))

    assert len({id(loop) for loop in loops_seen}) == 3
