"""Integration tests with real FastMCP provider (examples/provider_math)."""

import pytest

from mcp_hangar.models import ProviderSpec, ProviderState
from mcp_hangar.provider_manager import ProviderManager

# Skip all tests in this file for now - FastMCP requires different stdio handling
pytestmark = pytest.mark.skip(reason="FastMCP provider requires async/await handling - TBD")


def test_real_provider_lifecycle():
    """Test lifecycle with real FastMCP math provider."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # Start provider
    mgr.ensure_ready()
    assert mgr.conn.state == ProviderState.READY

    # Verify tools are discovered
    with mgr.conn.lock:
        tools = list(mgr.conn.tools.keys())

    # Real provider should have these tools
    assert "add" in tools
    assert "subtract" in tools
    assert "multiply" in tools
    assert "divide" in tools
    assert "power" in tools

    mgr.shutdown()


def test_real_provider_tool_invocation():
    """Test tool invocation with real provider."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # Test add
    result = mgr.invoke_tool("add", {"a": 5, "b": 3}, timeout=10.0)
    assert result["result"] == 8

    # Test subtract
    result = mgr.invoke_tool("subtract", {"a": 10, "b": 4}, timeout=10.0)
    assert result["result"] == 6

    # Test multiply
    result = mgr.invoke_tool("multiply", {"a": 7, "b": 6}, timeout=10.0)
    assert result["result"] == 42

    # Test divide
    result = mgr.invoke_tool("divide", {"a": 20, "b": 4}, timeout=10.0)
    assert result["result"] == 5.0

    # Test power
    result = mgr.invoke_tool("power", {"base": 2, "exponent": 10}, timeout=10.0)
    assert result["result"] == 1024

    mgr.shutdown()


def test_real_provider_division_by_zero():
    """Test error handling in real provider."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # Division by zero should raise error
    with pytest.raises(Exception):
        mgr.invoke_tool("divide", {"a": 10, "b": 0}, timeout=10.0)

    mgr.shutdown()


def test_real_provider_tool_schema():
    """Test that tool schemas are correctly discovered from real provider."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)
    mgr.ensure_ready()

    with mgr.conn.lock:
        # Check add tool schema
        add_tool = mgr.conn.tools.get("add")
        assert add_tool is not None
        assert "add" in add_tool.description.lower() or "Add" in add_tool.description
        assert add_tool.input_schema is not None

        # Verify input schema structure
        schema = add_tool.input_schema
        assert schema.get("type") == "object"
        assert "properties" in schema
        assert "a" in schema["properties"]
        assert "b" in schema["properties"]

    mgr.shutdown()


def test_real_provider_concurrent_invocations():
    """Test concurrent invocations on real provider."""
    import threading

    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    results = []
    errors = []

    def worker(tid):
        for i in range(10):
            try:
                result = mgr.invoke_tool("add", {"a": tid, "b": i}, timeout=15.0)
                results.append(result)
            except Exception as e:
                errors.append((tid, i, e))

    threads = []
    for tid in range(5):
        t = threading.Thread(target=worker, args=(tid,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify no errors
    assert len(errors) == 0, f"Errors occurred: {errors}"

    # Verify all 50 invocations succeeded (5 threads * 10 invocations)
    assert len(results) == 50

    # Verify metrics
    assert mgr.conn.health.total_invocations == 50
    assert mgr.conn.health.total_failures == 0

    mgr.shutdown()


def test_real_provider_health_check():
    """Test health check with real provider."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)
    mgr.ensure_ready()

    # Health check should succeed
    assert mgr.health_check() is True
    assert mgr.conn.state == ProviderState.READY

    mgr.shutdown()


def test_real_provider_recovery():
    """Test that real provider can recover after crash."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # First invocation
    result = mgr.invoke_tool("add", {"a": 1, "b": 2}, timeout=10.0)
    assert result["result"] == 3

    # Kill the provider
    old_pid = mgr.conn.client.process.pid
    mgr.conn.client.process.kill()
    mgr.conn.client.process.wait()

    # Mark as dead
    with mgr.conn.lock:
        mgr.conn.state = ProviderState.DEAD

    # Next invocation should restart
    result = mgr.invoke_tool("multiply", {"a": 5, "b": 7}, timeout=15.0)
    assert result["result"] == 35

    # Verify new process
    new_pid = mgr.conn.client.process.pid
    assert new_pid != old_pid

    mgr.shutdown()


def test_real_provider_floating_point_precision():
    """Test floating point operations with real provider."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # Test division with floats
    result = mgr.invoke_tool("divide", {"a": 7, "b": 3}, timeout=10.0)
    assert abs(result["result"] - 2.333333) < 0.0001

    # Test power with floats
    result = mgr.invoke_tool("power", {"base": 2.5, "exponent": 3}, timeout=10.0)
    assert abs(result["result"] - 15.625) < 0.0001

    mgr.shutdown()


def test_real_provider_large_numbers():
    """Test with large numbers."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # Test with large numbers
    result = mgr.invoke_tool("multiply", {"a": 999999999, "b": 888888888}, timeout=10.0)
    assert result["result"] == 999999999 * 888888888

    # Test power with large exponent
    result = mgr.invoke_tool("power", {"base": 2, "exponent": 20}, timeout=10.0)
    assert result["result"] == 1048576

    mgr.shutdown()


def test_real_provider_mixed_operations():
    """Test sequence of different operations."""
    spec = ProviderSpec(
        provider_id="real_math",
        mode="subprocess",
        command=["python", "-m", "examples.provider_math.server"],
    )

    mgr = ProviderManager(spec)

    # Perform calculation: ((10 + 5) * 3) / 5 ^ 2
    result1 = mgr.invoke_tool("add", {"a": 10, "b": 5}, timeout=10.0)
    assert result1["result"] == 15

    result2 = mgr.invoke_tool("multiply", {"a": result1["result"], "b": 3}, timeout=10.0)
    assert result2["result"] == 45

    result3 = mgr.invoke_tool("power", {"base": 5, "exponent": 2}, timeout=10.0)
    assert result3["result"] == 25

    result4 = mgr.invoke_tool("divide", {"a": result2["result"], "b": result3["result"]}, timeout=10.0)
    assert result4["result"] == 1.8

    # Verify metrics
    assert mgr.conn.health.total_invocations == 4
    assert mgr.conn.health.total_failures == 0

    mgr.shutdown()
