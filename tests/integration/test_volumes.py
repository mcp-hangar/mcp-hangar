#!/usr/bin/env python3
"""Test volumes with containers."""

from pathlib import Path
import sys

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mcp_hangar.domain.services.provider_launcher import ContainerLauncher  # noqa: E402


@pytest.mark.slow
def test_memory_volume():
    data_dir = Path("./data/memory").absolute()
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Data dir: {data_dir}")

    launcher = ContainerLauncher(runtime="auto")
    print(f"Runtime: {launcher.runtime}")

    # Memory uses /app/data for storage
    volumes = [f"{data_dir}:/app/data:rw"]
    print(f"Volumes: {volumes}")

    client = launcher.launch(
        image="mcp-memory:latest",
        volumes=volumes,
        read_only=False,
        network="none",
        memory_limit="256m",
    )
    print("Container started!")

    # Initialize
    resp = client.call(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        timeout=10,
    )
    print(f"Init: {resp.get('result', {}).get('serverInfo', {})}")

    # Create entity
    create_resp = client.call(
        "tools/call",
        {
            "name": "create_entities",
            "arguments": {
                "entities": [
                    {
                        "name": "VolumeTest",
                        "entityType": "test",
                        "observations": ["volume test data"],
                    }
                ]
            },
        },
        timeout=10,
    )

    if "error" in create_resp:
        print(f"ERROR: {create_resp['error']}")
    else:
        print("Create: OK")

    client.close()

    # Check if file was created
    memory_file = data_dir / "memory.jsonl"
    if memory_file.exists():
        print(f"✅ Data persisted to: {memory_file}")
        print(f"   Content: {memory_file.read_text()[:200]}...")
    else:
        print(f"❌ Memory file not found at {memory_file}")
        # List directory contents
        print(f"   Directory contents: {list(data_dir.iterdir())}")

    print("Done!")


if __name__ == "__main__":
    test_memory_volume()
