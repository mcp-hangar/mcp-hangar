"""Performance benchmarks for mcp-hangar proxy overhead.

Target: <5ms p99 proxy overhead (excluding downstream RPC).

Run benchmarks:
    uv run pytest tests/benchmark/ --benchmark-only --benchmark-sort=mean
    uv run pytest tests/benchmark/ --benchmark-only --benchmark-json=benchmark-results.json

Compare with baseline:
    uv run pytest tests/benchmark/ --benchmark-only --benchmark-compare=baseline.json
"""
