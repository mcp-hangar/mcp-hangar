#!/usr/bin/env python3
"""MCP Hangar performance benchmark suite.

Usage:
    uv run python scripts/benchmark.py --help
    uv run python scripts/benchmark.py --scenario cold_start
    uv run python scripts/benchmark.py --scenario throughput --duration 30
    uv run python scripts/benchmark.py --scenario concurrent --workers 50
"""

import argparse
import asyncio
from concurrent.futures import as_completed, ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

import httpx


@dataclass
class BenchmarkResult:
    """Benchmark result statistics."""

    name: str
    iterations: int
    total_time_s: float
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    throughput_rps: float

    def __str__(self) -> str:
        return f"""
{self.name}
{"=" * len(self.name)}
Iterations:  {self.iterations}
Total time:  {self.total_time_s:.2f}s
Throughput:  {self.throughput_rps:.2f} req/s

Latency (ms):
  Min:    {self.min_ms:.2f}
  Max:    {self.max_ms:.2f}
  Mean:   {self.mean_ms:.2f}
  Median: {self.median_ms:.2f}
  P95:    {self.p95_ms:.2f}
  P99:    {self.p99_ms:.2f}
"""


def percentile(data: list[float], p: float) -> float:
    """Calculate percentile."""
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def calculate_stats(name: str, latencies_ms: list[float], total_time_s: float) -> BenchmarkResult:
    """Calculate benchmark statistics."""
    return BenchmarkResult(
        name=name,
        iterations=len(latencies_ms),
        total_time_s=total_time_s,
        min_ms=min(latencies_ms),
        max_ms=max(latencies_ms),
        mean_ms=statistics.mean(latencies_ms),
        median_ms=statistics.median(latencies_ms),
        p95_ms=percentile(latencies_ms, 95),
        p99_ms=percentile(latencies_ms, 99),
        throughput_rps=len(latencies_ms) / total_time_s if total_time_s > 0 else 0,
    )


def benchmark_cold_start(iterations: int = 10, config_path: str = "../../config.yaml") -> BenchmarkResult:
    """Benchmark cold start time."""
    print(f"Running cold start benchmark ({iterations} iterations)...")

    latencies = []
    # MCP protocol requires initialize first, then tools/list
    init_request = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "benchmark", "version": "1.0.0"},
            },
            "id": 1,
        }
    )
    list_request = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 2})
    request = init_request + "\n" + list_request

    start_total = time.perf_counter()

    for i in range(iterations):
        start = time.perf_counter()

        # Run from packages/core directory
        core_dir = Path(__file__).parent.parent
        abs_config = (core_dir.parent.parent / config_path).resolve()

        result = subprocess.run(
            ["uv", "run", "mcp-hangar", "--config", str(abs_config), "serve"],
            input=request,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=core_dir,
            env={**os.environ, "VIRTUAL_ENV": ""},  # Clear to avoid uv warning
        )

        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)

        if result.returncode != 0:
            print(f"  [{i + 1}/{iterations}] FAILED: {result.stderr[:100]}")
        else:
            print(f"  [{i + 1}/{iterations}] {elapsed_ms:.2f}ms")

    total_time = time.perf_counter() - start_total
    return calculate_stats("Cold Start", latencies, total_time)


def benchmark_http_throughput(
    base_url: str = "http://localhost:8000",
    duration_s: int = 10,
    workers: int = 10,
) -> BenchmarkResult:
    """Benchmark HTTP endpoint throughput."""
    print(f"Running HTTP throughput benchmark ({duration_s}s, {workers} workers)...")

    latencies = []
    errors = 0
    stop_event = asyncio.Event()

    async def worker():
        nonlocal errors
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            while not stop_event.is_set():
                start = time.perf_counter()
                try:
                    response = await client.get("/health/live")
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    if response.status_code == 200:
                        latencies.append(elapsed_ms)
                    else:
                        errors += 1
                except Exception:
                    errors += 1

    async def run():
        tasks = [asyncio.create_task(worker()) for _ in range(workers)]
        await asyncio.sleep(duration_s)
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    start_total = time.perf_counter()
    asyncio.run(run())
    total_time = time.perf_counter() - start_total

    if errors:
        print(f"  Errors: {errors}")

    if not latencies:
        print("  No successful requests!")
        return BenchmarkResult("HTTP Throughput", 0, total_time, 0, 0, 0, 0, 0, 0, 0)

    return calculate_stats("HTTP Throughput", latencies, total_time)


def benchmark_tool_invocation(
    base_url: str = "http://localhost:8000",
    iterations: int = 100,
    workers: int = 10,
) -> BenchmarkResult:
    """Benchmark tool invocation throughput."""
    print(f"Running tool invocation benchmark ({iterations} calls, {workers} workers)...")

    latencies = []
    errors = 0

    def invoke_tool():
        nonlocal errors
        try:
            start = time.perf_counter()
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                response = client.post(
                    "/messages",
                    json={
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "hangar_status",
                            "arguments": {},
                        },
                        "id": 1,
                    },
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                if response.status_code == 200:
                    return elapsed_ms
                else:
                    errors += 1
                    return None
        except Exception:
            errors += 1
            return None

    start_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(invoke_tool) for _ in range(iterations)]
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result:
                latencies.append(result)
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{iterations}")

    total_time = time.perf_counter() - start_total

    if errors:
        print(f"  Errors: {errors}")

    if not latencies:
        print("  No successful invocations!")
        return BenchmarkResult("Tool Invocation", 0, total_time, 0, 0, 0, 0, 0, 0, 0)

    return calculate_stats("Tool Invocation", latencies, total_time)


def benchmark_batch_invocation(
    base_url: str = "http://localhost:8000",
    batch_size: int = 10,
    iterations: int = 20,
) -> BenchmarkResult:
    """Benchmark batch tool invocation."""
    print(f"Running batch invocation benchmark ({iterations} batches of {batch_size})...")

    latencies = []
    errors = 0

    for i in range(iterations):
        start = time.perf_counter()
        try:
            with httpx.Client(base_url=base_url, timeout=60.0) as client:
                response = client.post(
                    "/messages",
                    json={
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "hangar_batch",
                            "arguments": {
                                "calls": [{"tool": "hangar_status", "arguments": {}} for _ in range(batch_size)],
                            },
                        },
                        "id": 1,
                    },
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                if response.status_code == 200:
                    latencies.append(elapsed_ms)
                    print(f"  [{i + 1}/{iterations}] {elapsed_ms:.2f}ms ({batch_size} calls)")
                else:
                    errors += 1
                    print(f"  [{i + 1}/{iterations}] FAILED: {response.status_code}")
        except Exception as e:
            errors += 1
            print(f"  [{i + 1}/{iterations}] ERROR: {e}")

    total_time = sum(latencies) / 1000 if latencies else 0

    if not latencies:
        return BenchmarkResult("Batch Invocation", 0, total_time, 0, 0, 0, 0, 0, 0, 0)

    result = calculate_stats("Batch Invocation", latencies, total_time)
    # Adjust throughput to count individual calls
    result.throughput_rps = (len(latencies) * batch_size) / total_time if total_time > 0 else 0
    return result


def main():
    parser = argparse.ArgumentParser(description="MCP Hangar Performance Benchmark")
    parser.add_argument(
        "--scenario",
        choices=["cold_start", "throughput", "tool", "batch", "all"],
        default="all",
        help="Benchmark scenario to run",
    )
    parser.add_argument("--iterations", type=int, default=100, help="Number of iterations")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds (for throughput)")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL for HTTP tests")
    parser.add_argument("--batch-size", type=int, default=10, help="Batch size for batch tests")

    args = parser.parse_args()

    results = []

    if args.scenario in ("cold_start", "all"):
        results.append(benchmark_cold_start(iterations=min(args.iterations, 10)))

    if args.scenario in ("throughput", "all"):
        results.append(benchmark_http_throughput(args.url, args.duration, args.workers))

    if args.scenario in ("tool", "all"):
        results.append(benchmark_tool_invocation(args.url, args.iterations, args.workers))

    if args.scenario in ("batch", "all"):
        results.append(benchmark_batch_invocation(args.url, args.batch_size, args.iterations // 5))

    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)

    for result in results:
        print(result)


if __name__ == "__main__":
    main()
