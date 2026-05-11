#!/usr/bin/env python3
"""Quick MCP Hangar benchmark using stdio mode."""

import json
from pathlib import Path
import statistics
import subprocess
import time


def run_stdio_benchmark(iterations: int = 20):
    """Benchmark tool invocation via stdio."""
    print(f"Running stdio benchmark ({iterations} iterations)...")

    core_dir = Path(__file__).parent.parent
    config_path = core_dir.parent.parent / "config.yaml"

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
    request = init_request + "\n" + list_request + "\n"

    latencies = []

    for i in range(iterations):
        start = time.perf_counter()

        result = subprocess.run(
            ["uv", "run", "mcp-hangar", "--config", str(config_path), "serve"],
            input=request,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=core_dir,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        if result.returncode == 0 and "tools" in result.stdout:
            latencies.append(elapsed_ms)
            status = "OK"
        else:
            status = "FAIL"

        print(f"  [{i + 1}/{iterations}] {elapsed_ms:.1f}ms - {status}")

    if latencies:
        print(f"\n{'=' * 50}")
        print("RESULTS (tools/list via stdio)")
        print(f"{'=' * 50}")
        print(f"Iterations: {len(latencies)}/{iterations}")
        print(f"Min:        {min(latencies):.1f}ms")
        print(f"Max:        {max(latencies):.1f}ms")
        print(f"Mean:       {statistics.mean(latencies):.1f}ms")
        print(f"Median:     {statistics.median(latencies):.1f}ms")
        sorted_lat = sorted(latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        print(f"P95:        {sorted_lat[p95_idx] if p95_idx < len(sorted_lat) else sorted_lat[-1]:.1f}ms")
        print(f"Throughput: {len(latencies) / (sum(latencies) / 1000):.2f} req/s")


def run_tool_call_benchmark(iterations: int = 10):
    """Benchmark actual tool invocation (hangar_status)."""
    print(f"\nRunning tool call benchmark ({iterations} iterations)...")

    core_dir = Path(__file__).parent.parent
    config_path = core_dir.parent.parent / "config.yaml"

    # MCP protocol requires initialize first, then tools/call
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
    call_request = json.dumps(
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "hangar_status", "arguments": {}}, "id": 2}
    )
    request = init_request + "\n" + call_request + "\n"

    latencies = []

    for i in range(iterations):
        start = time.perf_counter()

        result = subprocess.run(
            ["uv", "run", "mcp-hangar", "--config", str(config_path), "serve"],
            input=request,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=core_dir,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        if result.returncode == 0 and "content" in result.stdout:
            latencies.append(elapsed_ms)
            status = "OK"
        else:
            status = "FAIL"

        print(f"  [{i + 1}/{iterations}] {elapsed_ms:.1f}ms - {status}")

    if latencies:
        print(f"\n{'=' * 50}")
        print("RESULTS (hangar_status tool call)")
        print(f"{'=' * 50}")
        print(f"Iterations: {len(latencies)}/{iterations}")
        print(f"Min:        {min(latencies):.1f}ms")
        print(f"Max:        {max(latencies):.1f}ms")
        print(f"Mean:       {statistics.mean(latencies):.1f}ms")
        print(f"Median:     {statistics.median(latencies):.1f}ms")


if __name__ == "__main__":
    run_stdio_benchmark(10)
    run_tool_call_benchmark(5)
