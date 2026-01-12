#!/usr/bin/env python3
"""Comprehensive test for all container-based MCP providers."""

import json
import logging
from pathlib import Path
import sys
import time

import pytest

# Add registry to path
sys.path.insert(0, str(Path(__file__).parent))

from mcp_hangar.domain.model import Provider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _run_provider_check(
    name: str,
    config: dict,
    test_tool: str,
    test_args: dict,
    expected_success: bool = True,
) -> None:
    """Generic provider check helper (not a pytest test)."""
    print(f"\n{'=' * 70}")
    print(f"🧪 Testing {name.upper()} Provider")
    print(f"{'=' * 70}")

    # Create provider
    provider = Provider(provider_id=name, **config)

    print(f"\n📦 Provider: {provider.provider_id}")
    print(f"   Mode: {provider._mode}")
    print(f"   Image: {provider._image if provider._image else 'building from Dockerfile'}")
    print(f"   Network: {provider._network}")
    print(f"   Read-only: {provider._read_only}")

    try:
        # Start provider
        print("\n🔄 Starting provider...")
        start_time = time.time()
        provider.ensure_ready()
        elapsed = time.time() - start_time
        print(f"   ✅ Provider started in {elapsed:.2f}s! State: {provider.state.value}")

        # List tools
        print("\n🔍 Discovering tools...")
        tools = provider.tools
        tool_list = list(tools)
        print(f"   ✅ Found {len(tool_list)} tools:")
        for tool in tool_list[:8]:
            desc = tool.description[:60] if len(tool.description) > 60 else tool.description
            print(f"      - {tool.name}: {desc}...")
        if len(tool_list) > 8:
            print(f"      ... and {len(tool_list) - 8} more")

        # Test tool invocation
        if test_tool and test_args:
            print(f"\n🔧 Testing tool: {test_tool}")
            print(f"   Arguments: {json.dumps(test_args, indent=2)[:100]}...")

            result = provider.invoke_tool(test_tool, test_args, timeout=15.0)

            if result.get("isError"):
                content = result.get("content", [{}])[0].get("text", "")
                if expected_success:
                    print(f"   ❌ FAILED: {content}")
                    assert False, f"{name} tool invocation failed: {content}"
                else:
                    print(f"   ✅ Expected error: {content}")
            else:
                if expected_success:
                    print("   ✅ SUCCESS!")
                    content = result.get("content", [{}])[0]
                    if "text" in content:
                        text = content["text"]
                        if len(text) > 200:
                            print(f"   Result: {text[:200]}...")
                        else:
                            print(f"   Result: {text}")
                else:
                    print("   ❌ Expected error but got success")
                    assert False, f"{name} expected error but got success"

        print(f"\n{'=' * 70}")
        print(f"✅ {name.upper()} provider test PASSED!")
        print(f"{'=' * 70}")

    except Exception:
        # Keep verbose output for debugging in CI
        import traceback

        traceback.print_exc()
        raise

    finally:
        # Stop provider
        print("\n🛑 Stopping provider...")
        try:
            provider.shutdown()
            print(f"   ✅ Provider stopped. Final state: {provider.state.value}")
        except Exception as e:
            print(f"   ⚠️  Error stopping provider: {e}")


@pytest.mark.slow
def test_filesystem():
    """Test filesystem provider with read-only home mount."""
    # Create a test file in home directory
    test_file = Path.home() / ".mcp_test_file.txt"
    try:
        test_file.write_text("MCP filesystem provider test")
        print(f"📝 Created test file: {test_file}")
    except Exception as e:
        print(f"⚠️  Could not create test file: {e}")

    # Get current UID:GID
    import os

    user = f"{os.getuid()}:{os.getgid()}"

    config = {
        "mode": "container",
        "build": {"dockerfile": "docker/Dockerfile.filesystem", "context": "."},
        "volumes": [f"{Path.home()}:/data:ro"],
        "resources": {"memory": "256m", "cpu": "0.5"},
        "network": "none",
        "user": user,  # Run as current user
        "idle_ttl_s": 300,
    }

    # Test reading a file
    test_args = {"path": "/data/.mcp_test_file.txt"}

    _run_provider_check("filesystem", config, "read_file", test_args)

    # Cleanup
    try:
        if test_file.exists():
            test_file.unlink()
            print("🗑️  Cleaned up test file")
    except Exception as e:
        print(f"⚠️  Could not cleanup test file: {e}")

    return None


@pytest.mark.slow
def test_memory():
    """Test memory provider with write access."""
    # Ensure data directory exists
    data_dir = Path("./data")
    data_dir.mkdir(exist_ok=True)

    config = {
        "mode": "container",
        "build": {"dockerfile": "docker/Dockerfile.memory", "context": "."},
        "volumes": ["./data:/app/data:rw"],
        "env": {"MEMORY_FILE_PATH": "/app/data/memory.jsonl"},
        "resources": {"memory": "512m", "cpu": "1.0"},
        "network": "none",
        "read_only": False,
        "idle_ttl_s": 600,
    }

    test_args = {
        "entities": [
            {
                "name": "test-provider-all",
                "entityType": "test",
                "observations": ["Testing all providers"],
            }
        ]
    }

    _run_provider_check("memory", config, "create_entities", test_args)


@pytest.mark.slow
def test_fetch():
    """Test fetch provider with network access."""
    config = {
        "mode": "container",
        "build": {"dockerfile": "docker/Dockerfile.fetch", "context": "."},
        "resources": {"memory": "256m", "cpu": "0.5"},
        "network": "bridge",  # Needs internet
        "idle_ttl_s": 300,
    }

    # Test fetching a simple URL - use the actual tool name
    # Use github.com instead of example.com (which may be blocked)
    test_args = {"url": "https://github.com"}

    _run_provider_check("fetch", config, "imageFetch", test_args)


@pytest.mark.slow
def test_math():
    """Test math provider (Python-based)."""
    config = {
        "mode": "container",
        "build": {"dockerfile": "docker/Dockerfile.math", "context": "."},
        "resources": {"memory": "128m", "cpu": "0.25"},
        "network": "none",
        "idle_ttl_s": 300,
    }

    test_args = {"a": 42, "b": 8}

    _run_provider_check("math", config, "add", test_args)


def main():
    """Run all provider tests."""
    print("\n" + "=" * 70)
    print("🚀 COMPREHENSIVE MCP PROVIDER TEST SUITE")
    print("=" * 70)
    print("\nTesting all container-based providers:")
    print("  1. FILESYSTEM - read-only home mount")
    print("  2. MEMORY - read-write data persistence")
    print("  3. FETCH - network access for web content")
    print("  4. MATH - isolated compute")
    print()

    results = {}

    # Test each provider
    print("\n" + "🔹" * 35)
    results["filesystem"] = test_filesystem()

    print("\n" + "🔹" * 35)
    results["memory"] = test_memory()

    print("\n" + "🔹" * 35)
    results["fetch"] = test_fetch()

    print("\n" + "🔹" * 35)
    results["math"] = test_math()

    # Summary
    print("\n" + "=" * 70)
    print("📊 TEST SUMMARY")
    print("=" * 70)

    for provider, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {provider:15s} {status}")

    all_passed = all(results.values())

    print("\n" + "=" * 70)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
    else:
        print("⚠️  SOME TESTS FAILED")
    print("=" * 70)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
