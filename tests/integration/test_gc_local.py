#!/usr/bin/env python3
"""
Test Garbage Collection and Health Monitoring locally.

Usage:
    python test_gc_local.py

This script demonstrates:
- Automatic shutdown of idle providers (GC)
- Health check monitoring
- Provider state transitions
"""

from pathlib import Path
import sys
import time

# Ensure the project is in path
sys.path.insert(0, str(Path(__file__).parent))


def main():
    print("=" * 60)
    print("🗑️  MCP Registry - GC & Health Monitor Test")
    print("=" * 60)

    from mcp_hangar.gc import BackgroundWorker
    from mcp_hangar.models import ProviderSpec
    from mcp_hangar.provider_manager import ProviderManager

    # Create provider with SHORT idle TTL for testing
    print("\n📦 Creating provider with 5s idle TTL...")
    spec = ProviderSpec(
        provider_id="gc_test",
        mode="subprocess",
        command=[sys.executable, "tests/mock_provider.py"],
        idle_ttl_s=5,  # Very short TTL for testing
        health_check_interval_s=2,
        max_consecutive_failures=3,
    )

    manager = ProviderManager(spec)
    providers = {"gc_test": manager}

    print(f"   Initial state: {manager.state}")

    # Start provider
    print("\n🔄 Starting provider...")
    manager.ensure_ready()
    print(f"   State: {manager.state}")
    print(f"   Alive: {manager.is_alive}")

    # Start GC worker
    print("\n🗑️  Starting GC worker (2s interval)...")
    gc_worker = BackgroundWorker(providers, interval_s=2, task="gc")
    gc_worker.start()

    # Start health check worker
    print("💓 Starting health check worker (3s interval)...")
    health_worker = BackgroundWorker(providers, interval_s=3, task="health_check")
    health_worker.start()

    # Use provider
    print("\n🧮 Using provider (keeps it alive)...")
    result = manager.invoke_tool("add", {"a": 100, "b": 200})
    print(f"   100 + 200 = {result.get('result', result)}")

    time.sleep(2)

    result = manager.invoke_tool("multiply", {"a": 7, "b": 8})
    print(f"   7 × 8 = {result.get('result', result)}")

    print(f"   State after use: {manager.state}")

    # Wait for idle timeout
    print("\n⏳ Waiting for idle timeout (8 seconds)...")
    print("   (Provider should be automatically shut down after 5s of inactivity)")

    for i in range(8):
        time.sleep(1)
        print(f"   [{i + 1}s] State: {manager.state}")
        if manager.state.value == "cold":
            print("   ✅ Provider was shut down by GC!")
            break

    final_state = manager.state

    # Stop workers
    print("\n🛑 Stopping workers...")
    gc_worker.stop()
    health_worker.stop()

    # Summary
    print("\n" + "=" * 60)
    print("📊 Test Results:")
    print(f"   Final state: {final_state}")

    if final_state.value == "cold":
        print("   ✅ GC test PASSED - provider was automatically shut down")
    else:
        print("   ⚠️  GC test - provider not shut down (may need longer wait)")

    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
