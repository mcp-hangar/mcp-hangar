"""Integration tests for the complete registry system."""

import sys
import threading
import time

from mcp_hangar.gc import BackgroundWorker
from mcp_hangar.models import ProviderSpec
from mcp_hangar.provider_manager import ProviderManager


def test_full_workflow():
    """Test complete workflow: start, discover, invoke, stop."""
    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # 1. Start provider
    mgr.ensure_ready()
    assert mgr.conn.state.value == "ready"

    # 2. List tools
    with mgr.conn.lock:
        tools = list(mgr.conn.tools.keys())
    assert "add" in tools
    assert "subtract" in tools
    assert "multiply" in tools

    # 3. Invoke tools
    result = mgr.invoke_tool("add", {"a": 10, "b": 20}, timeout=5.0)
    assert result["result"] == 30

    result = mgr.invoke_tool("multiply", {"a": 5, "b": 7}, timeout=5.0)
    assert result["result"] == 35

    # 4. Check health metrics
    # Note: total_invocations includes internal calls (ensure_ready, tools/list)
    assert mgr.conn.health.total_invocations >= 2
    assert mgr.conn.health.total_failures == 0

    # 5. Stop provider
    mgr.shutdown()
    assert mgr.conn.state.value == "cold"


def test_gc_worker():
    """Test garbage collection worker."""
    providers = {}

    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
        idle_ttl_s=1,  # Short TTL for testing
    )

    mgr = ProviderManager(spec)
    providers["math"] = mgr

    # Start provider
    mgr.ensure_ready()
    assert mgr.conn.state.value == "ready"

    # Start GC worker with short interval
    gc_worker = BackgroundWorker(providers, interval_s=1, task="gc")
    gc_worker.start()

    # Wait for idle timeout
    time.sleep(2)

    # GC should have shut down the idle provider
    assert mgr.conn.state.value == "cold"

    gc_worker.stop()


def test_health_check_worker():
    """Test health check worker."""
    providers = {}

    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)
    providers["math"] = mgr

    # Start provider
    mgr.ensure_ready()
    assert mgr.conn.state.value == "ready"

    # Start health check worker
    health_worker = BackgroundWorker(providers, interval_s=1, task="health_check")
    health_worker.start()

    # Let health checks run
    time.sleep(2)

    # Provider should still be healthy
    assert mgr.conn.state.value == "ready"

    health_worker.stop()
    mgr.shutdown()


def test_concurrent_stress():
    """Stress test with concurrent access from multiple threads."""
    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    results = []
    errors = []

    def worker(thread_id):
        for i in range(20):
            try:
                result = mgr.invoke_tool("add", {"a": thread_id, "b": i}, timeout=10.0)
                results.append(result)
            except Exception as e:
                errors.append((thread_id, i, e))

    # Spawn 10 threads, each making 20 requests
    threads = []
    for tid in range(10):
        t = threading.Thread(target=worker, args=(tid,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify no errors
    assert len(errors) == 0, f"Errors occurred: {errors[:5]}"

    # Verify we got all 200 results (10 threads * 20 requests)
    assert len(results) == 200

    # Verify health metrics
    # Note: total_invocations includes internal calls (ensure_ready, tools/list)
    assert mgr.conn.health.total_invocations >= 200
    assert mgr.conn.health.total_failures == 0

    mgr.shutdown()


def test_provider_recovery_after_kill():
    """Test that provider recovers after being killed."""
    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # Start and invoke
    result = mgr.invoke_tool("add", {"a": 1, "b": 2}, timeout=5.0)
    assert result["result"] == 3

    # Kill the process
    old_pid = mgr.conn.client.process.pid
    mgr.conn.client.process.kill()
    mgr.conn.client.process.wait()

    # Mark as dead
    from mcp_hangar.models import ProviderState

    with mgr.conn.lock:
        mgr.conn.state = ProviderState.DEAD

    # Next invocation should restart the provider
    result = mgr.invoke_tool("multiply", {"a": 3, "b": 4}, timeout=10.0)
    assert result["result"] == 12

    # Should have a new PID
    new_pid = mgr.conn.client.process.pid
    assert new_pid != old_pid

    mgr.shutdown()


def test_error_handling():
    """Test error handling and health tracking."""
    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # Successful call
    result = mgr.invoke_tool("add", {"a": 5, "b": 3}, timeout=5.0)
    assert result["result"] == 8

    # Error call (division by zero)
    try:
        mgr.invoke_tool("divide", {"a": 10, "b": 0}, timeout=5.0)
        assert False, "Should have raised error"
    except Exception:
        pass

    # Another successful call
    result = mgr.invoke_tool("subtract", {"a": 10, "b": 3}, timeout=5.0)
    assert result["result"] == 7

    # Verify metrics
    # Note: total_invocations includes internal calls (ensure_ready, tools/list)
    assert mgr.conn.health.total_invocations >= 3
    assert mgr.conn.health.total_failures >= 1
    assert mgr.conn.health.consecutive_failures == 0  # Reset after successful call

    mgr.shutdown()


def test_multiple_providers_isolation():
    """Test that multiple providers operate in isolation."""
    providers = {}

    for i in range(3):
        spec = ProviderSpec(
            provider_id=f"math{i}",
            mode="subprocess",
            command=[sys.executable, "tests/mock_provider.py"],
        )
        providers[f"math{i}"] = ProviderManager(spec)

    # Start all providers
    for mgr in providers.values():
        mgr.ensure_ready()

    # Invoke on different providers
    result0 = providers["math0"].invoke_tool("add", {"a": 1, "b": 1}, timeout=5.0)
    result1 = providers["math1"].invoke_tool("multiply", {"a": 2, "b": 3}, timeout=5.0)
    result2 = providers["math2"].invoke_tool("power", {"base": 2, "exponent": 3}, timeout=5.0)

    assert result0["result"] == 2
    assert result1["result"] == 6
    assert result2["result"] == 8

    # Each should have at least 1 invocation (may include internal calls)
    for mgr in providers.values():
        assert mgr.conn.health.total_invocations >= 1

    # Cleanup
    for mgr in providers.values():
        mgr.shutdown()


def test_tool_schema_discovery():
    """Test that tool schemas are properly discovered."""
    spec = ProviderSpec(
        provider_id="math",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)
    mgr.ensure_ready()

    # Verify tool schemas are populated
    with mgr.conn.lock:
        assert "add" in mgr.conn.tools
        add_tool = mgr.conn.tools["add"]

        assert add_tool.name == "add"
        assert "add two numbers" in add_tool.description.lower()
        assert add_tool.input_schema is not None

    mgr.shutdown()
