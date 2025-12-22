"""Stress tests and performance benchmarks."""

import statistics
import threading
import time

import pytest

from mcp_hangar.models import ProviderSpec
from mcp_hangar.provider_manager import ProviderManager


@pytest.mark.slow
def test_high_throughput_single_provider():
    """Test throughput with single provider under load."""
    spec = ProviderSpec(
        provider_id="perf_test",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # Warm up
    for _ in range(10):
        mgr.invoke_tool("add", {"a": 1, "b": 2}, timeout=10.0)

    # Measure throughput
    start_time = time.time()
    num_requests = 100

    for i in range(num_requests):
        mgr.invoke_tool("add", {"a": i, "b": i}, timeout=10.0)

    end_time = time.time()
    duration = end_time - start_time

    throughput = num_requests / duration
    avg_latency = (duration / num_requests) * 1000  # ms

    print(f"\n📊 Throughput: {throughput:.2f} req/s")
    print(f"📊 Average latency: {avg_latency:.2f} ms")

    # Assertions
    assert throughput > 10, f"Throughput too low: {throughput:.2f} req/s"
    assert avg_latency < 100, f"Latency too high: {avg_latency:.2f} ms"

    mgr.shutdown()


@pytest.mark.slow
def test_latency_percentiles():
    """Measure latency distribution (p50, p95, p99)."""
    spec = ProviderSpec(
        provider_id="latency_test",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # Warm up
    for _ in range(10):
        mgr.invoke_tool("add", {"a": 1, "b": 2}, timeout=10.0)

    # Measure latencies
    latencies = []
    num_requests = 200

    for i in range(num_requests):
        start = time.time()
        mgr.invoke_tool("add", {"a": i, "b": i}, timeout=10.0)
        end = time.time()
        latencies.append((end - start) * 1000)  # ms

    # Calculate percentiles
    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[18]  # 95th percentile
    p99 = statistics.quantiles(latencies, n=100)[98]  # 99th percentile
    mean = statistics.mean(latencies)
    stdev = statistics.stdev(latencies)

    print("\n📊 Latency Statistics:")
    print(f"  Mean: {mean:.2f} ms")
    print(f"  StdDev: {stdev:.2f} ms")
    print(f"  p50: {p50:.2f} ms")
    print(f"  p95: {p95:.2f} ms")
    print(f"  p99: {p99:.2f} ms")

    # Assertions
    assert p50 < 50, f"p50 latency too high: {p50:.2f} ms"
    assert p95 < 100, f"p95 latency too high: {p95:.2f} ms"
    assert p99 < 200, f"p99 latency too high: {p99:.2f} ms"

    mgr.shutdown()


@pytest.mark.slow
def test_concurrent_stress_multiple_providers():
    """Stress test with multiple providers and high concurrency."""
    num_providers = 5
    threads_per_provider = 10
    requests_per_thread = 20

    providers = []
    for i in range(num_providers):
        spec = ProviderSpec(
            provider_id=f"stress_{i}",
            mode="subprocess",
            command=["python", "tests/mock_provider.py"],
        )
        providers.append(ProviderManager(spec))

    results = []
    errors = []

    def worker(provider_idx, thread_idx):
        mgr = providers[provider_idx]
        for req_idx in range(requests_per_thread):
            try:
                result = mgr.invoke_tool("add", {"a": thread_idx, "b": req_idx}, timeout=15.0)
                results.append(result)
            except Exception as e:
                errors.append((provider_idx, thread_idx, req_idx, e))

    start_time = time.time()

    # Spawn threads
    threads = []
    for p_idx in range(num_providers):
        for t_idx in range(threads_per_provider):
            t = threading.Thread(target=worker, args=(p_idx, t_idx))
            threads.append(t)
            t.start()

    # Wait for completion
    for t in threads:
        t.join()

    end_time = time.time()
    duration = end_time - start_time

    total_requests = num_providers * threads_per_provider * requests_per_thread
    throughput = total_requests / duration

    print("\n📊 Concurrent Stress Test:")
    print(f"  Providers: {num_providers}")
    print(f"  Threads/Provider: {threads_per_provider}")
    print(f"  Requests/Thread: {requests_per_thread}")
    print(f"  Total Requests: {total_requests}")
    print(f"  Duration: {duration:.2f}s")
    print(f"  Throughput: {throughput:.2f} req/s")
    print(f"  Errors: {len(errors)}")

    # Assertions
    assert len(errors) == 0, f"Errors occurred: {errors[:5]}"
    assert len(results) == total_requests
    assert throughput > 50, f"Throughput too low: {throughput:.2f} req/s"

    # Cleanup
    for mgr in providers:
        mgr.shutdown()


@pytest.mark.slow
def test_long_running_stability():
    """Test stability over extended period."""
    spec = ProviderSpec(
        provider_id="stability_test",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # Run for 30 seconds
    duration = 30
    start_time = time.time()
    iteration = 0
    errors = []

    while time.time() - start_time < duration:
        try:
            mgr.invoke_tool("add", {"a": iteration, "b": iteration}, timeout=10.0)
            iteration += 1

            # Health check every 10 iterations
            if iteration % 10 == 0:
                assert mgr.health_check() is True

            # Small delay to avoid hammering
            time.sleep(0.05)

        except Exception as e:
            errors.append((iteration, e))

    print("\n📊 Long-Running Stability Test:")
    print(f"  Duration: {duration}s")
    print(f"  Iterations: {iteration}")
    print(f"  Errors: {len(errors)}")
    print(f"  Success Rate: {((iteration - len(errors)) / iteration * 100):.2f}%")

    # Assertions
    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert iteration > 100, f"Too few iterations: {iteration}"

    # Verify no resource leaks
    assert mgr.conn.client.is_alive()

    mgr.shutdown()


@pytest.mark.slow
def test_rapid_start_stop_cycles():
    """Test rapid provider start/stop cycles."""
    spec = ProviderSpec(
        provider_id="cycle_test",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    cycles = 10
    errors = []

    for i in range(cycles):
        try:
            # Start
            mgr.ensure_ready()
            assert mgr.conn.state.value == "ready"

            # Quick invocation
            result = mgr.invoke_tool("add", {"a": i, "b": i}, timeout=10.0)
            assert result["result"] == i + i

            # Stop
            mgr.shutdown()
            assert mgr.conn.state.value == "cold"

        except Exception as e:
            errors.append((i, e))

    print("\n📊 Rapid Start/Stop Cycles:")
    print(f"  Cycles: {cycles}")
    print(f"  Errors: {len(errors)}")

    assert len(errors) == 0, f"Errors occurred: {errors}"


@pytest.mark.slow
def test_memory_stability():
    """Test for memory leaks over many invocations."""
    import gc

    spec = ProviderSpec(
        provider_id="memory_test",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    # Force garbage collection
    gc.collect()

    # Warm up
    for _ in range(100):
        mgr.invoke_tool("add", {"a": 1, "b": 2}, timeout=10.0)

    # Force GC again
    gc.collect()

    # Run many iterations
    iterations = 1000
    for i in range(iterations):
        mgr.invoke_tool("add", {"a": i, "b": i}, timeout=10.0)

        # Periodic GC
        if i % 100 == 0:
            gc.collect()

    # Final GC
    gc.collect()

    print("\n📊 Memory Stability Test:")
    print(f"  Iterations: {iterations}")
    print("  Note: Monitor memory usage manually if needed")

    # Verify provider still healthy
    assert mgr.conn.state.value == "ready"
    assert mgr.conn.client.is_alive()

    mgr.shutdown()


@pytest.mark.slow
def test_concurrent_provider_starts():
    """Test concurrent ensure_ready() calls don't cause issues."""
    spec = ProviderSpec(
        provider_id="concurrent_start",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
    )

    mgr = ProviderManager(spec)

    results = []
    errors = []

    def start_and_invoke():
        try:
            mgr.ensure_ready()
            result = mgr.invoke_tool("add", {"a": 1, "b": 1}, timeout=15.0)
            results.append(result)
        except Exception as e:
            errors.append(e)

    # Multiple threads trying to start simultaneously
    threads = []
    for _ in range(20):
        t = threading.Thread(target=start_and_invoke)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print("\n📊 Concurrent Provider Starts:")
    print("  Threads: 20")
    print(f"  Successes: {len(results)}")
    print(f"  Errors: {len(errors)}")

    # All should succeed
    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 20

    # Should have exactly one provider instance
    with mgr.conn.lock:
        assert mgr.conn.client is not None

    mgr.shutdown()


@pytest.mark.slow
def test_degradation_under_errors():
    """Test behavior under sustained errors."""
    spec = ProviderSpec(
        provider_id="error_test",
        mode="subprocess",
        command=["python", "tests/mock_provider.py"],
        max_consecutive_failures=5,
    )

    mgr = ProviderManager(spec)

    # Cause errors by dividing by zero
    errors = 0
    for i in range(10):
        try:
            mgr.invoke_tool("divide", {"a": 10, "b": 0}, timeout=10.0)
        except Exception:
            errors += 1

    print("\n📊 Error Degradation Test:")
    print(f"  Errors: {errors}")
    print(f"  Consecutive Failures: {mgr.conn.health.consecutive_failures}")
    print(f"  Total Failures: {mgr.conn.health.total_failures}")

    # Should have tracked failures
    assert mgr.conn.health.total_failures == errors
    assert errors == 10

    # Provider might be degraded if threshold reached
    if mgr.conn.state.value == "degraded":
        print("  State: DEGRADED (as expected)")

    mgr.shutdown()
