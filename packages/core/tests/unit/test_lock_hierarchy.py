"""Tests for lock hierarchy module."""

import threading

import pytest

from mcp_hangar.infrastructure.lock_hierarchy import (
    clear_thread_locks,
    get_current_thread_locks,
    LockLevel,
    LockOrderViolation,
    TrackedLock,
    TrackedRLock,
)


class TestLockLevel:
    """Tests for LockLevel enum."""

    def test_lock_levels_are_ordered(self):
        """Lock levels should be in ascending order."""
        assert LockLevel.PROVIDER < LockLevel.EVENT_BUS
        assert LockLevel.EVENT_BUS < LockLevel.EVENT_STORE
        assert LockLevel.EVENT_STORE < LockLevel.SAGA_MANAGER
        assert LockLevel.SAGA_MANAGER < LockLevel.STDIO_CLIENT

    def test_provider_is_highest_priority(self):
        """PROVIDER should have the lowest numeric value (highest priority)."""
        assert LockLevel.PROVIDER == 10
        assert all(level >= LockLevel.PROVIDER for level in LockLevel)

    def test_stdio_client_is_lowest_priority(self):
        """STDIO_CLIENT should be acquired last."""
        assert LockLevel.STDIO_CLIENT == 50


class TestTrackedLock:
    """Tests for TrackedLock functionality."""

    def setup_method(self):
        """Clear thread locks before each test."""
        clear_thread_locks()

    def test_basic_acquire_release(self):
        """Basic lock acquire and release should work."""
        lock = TrackedLock(LockLevel.PROVIDER, "test-provider")

        lock.acquire()
        assert len(get_current_thread_locks()) == 1

        lock.release()
        assert len(get_current_thread_locks()) == 0

    def test_context_manager(self):
        """Lock should work as context manager."""
        lock = TrackedLock(LockLevel.PROVIDER, "test-provider")

        with lock:
            assert len(get_current_thread_locks()) == 1

        assert len(get_current_thread_locks()) == 0

    def test_valid_acquisition_order(self):
        """Acquiring locks in ascending order should succeed."""
        lock1 = TrackedLock(LockLevel.PROVIDER, "provider")
        lock2 = TrackedLock(LockLevel.EVENT_BUS, "event-bus")
        lock3 = TrackedLock(LockLevel.STDIO_CLIENT, "client")

        with lock1:
            with lock2:
                with lock3:
                    held = get_current_thread_locks()
                    assert len(held) == 3

    def test_invalid_acquisition_order_raises(self):
        """Acquiring locks in wrong order should raise LockOrderViolation."""
        lock_low = TrackedLock(LockLevel.STDIO_CLIENT, "client")
        lock_high = TrackedLock(LockLevel.PROVIDER, "provider")

        with lock_low:
            with pytest.raises(LockOrderViolation) as exc_info:
                lock_high.acquire()

            assert exc_info.value.current_level == LockLevel.STDIO_CLIENT
            assert exc_info.value.requested_level == LockLevel.PROVIDER

    def test_reentrant_lock_allows_reacquire(self):
        """Reentrant lock should allow acquiring same lock multiple times."""
        lock = TrackedLock(LockLevel.EVENT_STORE, "store", reentrant=True)

        with lock:
            with lock:  # Should not raise
                with lock:  # Should not raise
                    held = get_current_thread_locks()
                    # All three acquisitions tracked
                    assert len(held) == 3

    def test_non_reentrant_lock_different_instance_same_level(self):
        """Non-reentrant locks at same level should raise."""
        lock1 = TrackedLock(LockLevel.PROVIDER, "provider-1", reentrant=False)
        lock2 = TrackedLock(LockLevel.PROVIDER, "provider-2", reentrant=False)

        with lock1:
            with pytest.raises(LockOrderViolation):
                lock2.acquire()

    def test_lock_properties(self):
        """Lock properties should return correct values."""
        lock = TrackedLock(LockLevel.EVENT_BUS, "my-bus", reentrant=False)

        assert lock.level == LockLevel.EVENT_BUS
        assert lock.name == "my-bus"

    def test_acquire_with_timeout(self):
        """Acquire with timeout should work."""
        lock = TrackedLock(LockLevel.PROVIDER, "test")

        acquired = lock.acquire(timeout=0.1)
        assert acquired

        lock.release()


class TestTrackedRLock:
    """Tests for TrackedRLock (reentrant convenience class)."""

    def setup_method(self):
        clear_thread_locks()

    def test_is_reentrant_by_default(self):
        """TrackedRLock should be reentrant."""
        lock = TrackedRLock(LockLevel.PROVIDER, "test")

        with lock:
            with lock:  # Should not raise
                assert len(get_current_thread_locks()) == 2


class TestCrossThreadBehavior:
    """Tests for lock behavior across threads."""

    def setup_method(self):
        clear_thread_locks()

    def test_locks_are_thread_local(self):
        """Each thread should have its own lock tracking."""
        # Use separate locks to avoid blocking between threads
        lock_main = TrackedLock(LockLevel.PROVIDER, "main-lock")
        lock_thread = TrackedLock(LockLevel.PROVIDER, "thread-lock")
        results = []

        def thread_func():
            with lock_thread:
                # Thread sees only its own lock
                held = get_current_thread_locks()
                results.append(len(held))

        # Main thread acquires its lock
        with lock_main:
            main_held_before = len(get_current_thread_locks())

            # Start another thread with different lock
            t = threading.Thread(target=thread_func)
            t.start()
            t.join()

            # Main thread should still see only 1 (its own)
            assert len(get_current_thread_locks()) == main_held_before

        # Other thread should have seen 1 (its own)
        assert results == [1]

    def test_different_threads_can_violate_order_independently(self):
        """Lock order is only enforced within same thread."""
        # Use separate lock instances to avoid cross-thread blocking
        lock_high_main = TrackedLock(LockLevel.PROVIDER, "provider-main")
        lock_high_thread = TrackedLock(LockLevel.PROVIDER, "provider-thread")
        lock_low_thread = TrackedLock(LockLevel.STDIO_CLIENT, "client-thread")
        error_raised = []

        def thread_func():
            try:
                # This thread acquires in wrong order (should raise)
                with lock_low_thread:
                    with lock_high_thread:
                        pass
            except LockOrderViolation:
                error_raised.append(True)

        # Main thread acquires in correct order
        with lock_high_main:
            t = threading.Thread(target=thread_func)
            t.start()
            t.join()

        # Other thread should have raised
        assert error_raised == [True]


class TestLockOrderViolation:
    """Tests for LockOrderViolation exception."""

    def test_exception_contains_details(self):
        """Exception should contain useful debugging info."""
        exc = LockOrderViolation(
            "test message",
            current_level=50,
            requested_level=10,
            held_locks=["lock1", "lock2"],
        )

        assert exc.current_level == 50
        assert exc.requested_level == 10
        assert exc.held_locks == ["lock1", "lock2"]
        assert "test message" in str(exc)


class TestIntegrationWithRealComponents:
    """Integration tests simulating real usage patterns."""

    def setup_method(self):
        clear_thread_locks()

    def test_provider_then_client_pattern(self):
        """Simulate Provider -> StdioClient lock acquisition."""
        provider_lock = TrackedLock(LockLevel.PROVIDER, "Provider:math")
        client_lock = TrackedLock(LockLevel.STDIO_CLIENT, "StdioClient:12345", reentrant=False)

        with provider_lock:
            # Copy reference under lock, then release for I/O
            pass

        # Client lock acquired after provider lock released - OK
        with client_lock:
            pass

    def test_event_bus_then_store_pattern(self):
        """Simulate EventBus -> EventStore pattern."""
        bus_lock = TrackedLock(LockLevel.EVENT_BUS, "EventBus", reentrant=False)
        store_lock = TrackedLock(LockLevel.EVENT_STORE, "EventStore", reentrant=True)

        with bus_lock:
            with store_lock:
                # This is valid - EVENT_BUS (20) < EVENT_STORE (30)
                pass

    def test_forbidden_store_then_bus_pattern(self):
        """EventStore -> EventBus should be forbidden."""
        bus_lock = TrackedLock(LockLevel.EVENT_BUS, "EventBus", reentrant=False)
        store_lock = TrackedLock(LockLevel.EVENT_STORE, "EventStore", reentrant=True)

        with store_lock:
            with pytest.raises(LockOrderViolation):
                bus_lock.acquire()
