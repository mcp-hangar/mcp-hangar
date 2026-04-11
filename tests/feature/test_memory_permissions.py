#!/usr/bin/env python3
"""Test memory provider with container mode to verify permissions fix."""

import json
import logging
from pathlib import Path
import sys

import pytest

# Add registry to path
sys.path.insert(0, str(Path(__file__).parent))

from mcp_hangar.domain.model import Provider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@pytest.mark.slow
def test_memory_container() -> None:
    """Test memory provider in container mode with volume mount."""

    print("\n" + "=" * 70)
    print("🧪 Testing Memory Provider - Container Mode")
    print("=" * 70)

    # Ensure data directory exists
    data_dir = Path("./data")
    data_dir.mkdir(exist_ok=True)
    logger.info(f"Data directory: {data_dir.absolute()}")

    # Create provider with container mode
    provider = Provider(
        provider_id="memory",
        mode="container",
        image="mcp-memory:latest",
        volumes=["./data:/app/data:rw"],
        env={"MEMORY_FILE_PATH": "/app/data/memory.jsonl"},
        resources={"memory": "512m", "cpu": "1.0"},
        network="none",
        read_only=False,  # Allow writes to /app/data
        idle_ttl_s=300,
    )

    print(f"\n📦 Provider created: {provider.provider_id}")
    print(f"   Mode: {provider._mode}")
    print(f"   Image: {provider._image}")
    print(f"   Volumes: {provider._volumes}")
    print(f"   Initial state: {provider.state.value}")

    try:
        # Start provider
        print("\n🔄 Starting provider...")
        provider.ensure_ready()
        print(f"   ✅ Provider started! State: {provider.state.value}")

        # List tools
        print("\n🔍 Discovering tools...")
        tools = provider.tools
        print(f"   ✅ Found {len(tools)} tools:")
        for tool in list(tools)[:5]:  # Show first 5
            print(f"      - {tool.name}: {tool.description[:60]}...")

        # Test create_entities (this was failing with permission denied)
        print("\n📝 Testing create_entities (write operation)...")
        result = provider.invoke_tool(
            "create_entities",
            {
                "entities": [
                    {
                        "name": "test-calculation",
                        "entityType": "calculation",
                        "observations": ["Testing memory persistence in container mode"],
                    }
                ]
            },
            timeout=10.0,
        )

        if result.get("isError"):
            content = result.get("content", [{}])[0].get("text", "")
            assert False, f"create_entities failed: {content}"
        else:
            print("   ✅ SUCCESS! Entity created")
            print(f"   Result: {json.dumps(result, indent=2)[:200]}...")

        # Test read_graph
        print("\n📖 Testing read_graph (read operation)...")
        result = provider.invoke_tool("read_graph", {}, timeout=10.0)

        if result.get("isError"):
            content = result.get("content", [{}])[0].get("text", "")
            assert False, f"read_graph failed: {content}"
        else:
            print("   ✅ SUCCESS! Graph read")
            content = result.get("content", [{}])[0]
            if "text" in content:
                data = json.loads(content["text"])
                entities = data.get("entities", [])
                print(f"   Found {len(entities)} entities in graph")

        # Check if memory.jsonl was created on host
        memory_file = data_dir / "memory.jsonl"
        if memory_file.exists():
            print(f"\n✅ Memory file created on host: {memory_file}")
            print(f"   Size: {memory_file.stat().st_size} bytes")
        else:
            print(f"\n⚠️  Memory file not found on host: {memory_file}")

        print("\n" + "=" * 70)
        print("✅ All tests passed! Permission issue is FIXED!")
        print("=" * 70)

        return None

    except Exception:
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


if __name__ == "__main__":
    success = test_memory_container()
    sys.exit(0 if success else 1)
