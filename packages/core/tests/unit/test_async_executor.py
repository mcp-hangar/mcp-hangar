"""Tests for AsyncExecutor module."""

import asyncio
import time

from mcp_hangar.infrastructure.async_executor import AsyncExecutor, submit_async


class TestAsyncExecutor:
    """Tests for AsyncExecutor class."""

    def test_singleton_instance(self):
        """Test that get_instance returns singleton."""
        instance1 = AsyncExecutor.get_instance()
        instance2 = AsyncExecutor.get_instance()
        assert instance1 is instance2

    def test_submit_executes_coroutine(self):
        """Test that submitted coroutine is executed."""
        executor = AsyncExecutor(max_workers=2)
        result_holder = {"value": None}

        async def set_value():
            result_holder["value"] = 42
            return 42

        executor.submit(set_value())

        # Wait for execution
        time.sleep(0.1)

        assert result_holder["value"] == 42
        executor.shutdown()

    def test_submit_calls_on_success_callback(self):
        """Test that on_success callback is called with result."""
        executor = AsyncExecutor(max_workers=2)
        callback_result = {"value": None}

        async def return_value():
            return "success"

        def on_success(result):
            callback_result["value"] = result

        executor.submit(return_value(), on_success=on_success)

        # Wait for execution
        time.sleep(0.1)

        assert callback_result["value"] == "success"
        executor.shutdown()

    def test_submit_calls_on_error_callback(self):
        """Test that on_error callback is called on exception."""
        executor = AsyncExecutor(max_workers=2)
        error_holder = {"error": None}

        async def raise_error():
            raise ValueError("test error")

        def on_error(e):
            error_holder["error"] = str(e)

        executor.submit(raise_error(), on_error=on_error)

        # Wait for execution
        time.sleep(0.1)

        assert error_holder["error"] == "test error"
        executor.shutdown()

    def test_submit_without_error_callback_logs_debug(self):
        """Test that errors without callback are logged (not raised)."""
        executor = AsyncExecutor(max_workers=2)

        async def raise_error():
            raise ValueError("test error")

        # Should not raise
        executor.submit(raise_error())

        # Wait for execution
        time.sleep(0.1)

        executor.shutdown()

    def test_shutdown_stops_executor(self):
        """Test that shutdown stops the executor."""
        executor = AsyncExecutor(max_workers=2)

        # Start executor
        async def noop():
            pass

        executor.submit(noop())
        time.sleep(0.05)

        # Shutdown
        executor.shutdown()

        assert executor._executor is None
        assert executor._started is False


class TestSubmitAsyncFunction:
    """Tests for submit_async convenience function."""

    def test_submit_async_executes_coroutine(self):
        """Test that submit_async executes the coroutine."""
        result_holder = {"value": None}

        async def set_value():
            result_holder["value"] = "executed"

        submit_async(set_value())

        # Wait for execution
        time.sleep(0.1)

        assert result_holder["value"] == "executed"

    def test_submit_async_with_error_callback(self):
        """Test submit_async with error callback."""
        error_holder = {"error": None}

        async def raise_error():
            raise RuntimeError("async error")

        def on_error(e):
            error_holder["error"] = str(e)

        submit_async(raise_error(), on_error=on_error)

        # Wait for execution
        time.sleep(0.1)

        assert error_holder["error"] == "async error"


class TestAsyncExecutorThreadSafety:
    """Tests for thread safety of AsyncExecutor."""

    def test_concurrent_submissions(self):
        """Test that multiple concurrent submissions work correctly."""
        executor = AsyncExecutor(max_workers=4)
        results = []

        async def append_value(value):
            await asyncio.sleep(0.01)
            results.append(value)

        # Submit multiple coroutines
        for i in range(10):
            executor.submit(append_value(i))

        # Wait for all to complete
        time.sleep(0.5)

        # All values should be present (order may vary)
        assert len(results) == 10
        assert set(results) == set(range(10))

        executor.shutdown()
