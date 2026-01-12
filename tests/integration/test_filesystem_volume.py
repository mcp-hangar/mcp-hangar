#!/usr/bin/env python3
"""Test filesystem volume persistence."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mcp_hangar.domain.services.provider_launcher import ContainerLauncher  # noqa: E402


@pytest.mark.slow
def test_filesystem_volume():
    data_dir = Path("./data/filesystem").absolute()
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o777)
    print(f"Data dir: {data_dir}")

    launcher = ContainerLauncher(runtime="auto")
    print(f"Runtime: {launcher.runtime}")

    volumes = [f"{data_dir}:/data:rw"]
    print(f"Volumes: {volumes}")

    client = launcher.launch(
        image="mcp-filesystem:latest",
        volumes=volumes,
        read_only=False,
        network="none",
        memory_limit="256m",
    )
    print("Container started!")

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

    write_resp = client.call(
        "tools/call",
        {
            "name": "write_file",
            "arguments": {
                "path": "/data/test_persistence.txt",
                "content": "This file should persist!",
            },
        },
        timeout=10,
    )

    if "error" in write_resp:
        print(f"Write ERROR: {write_resp['error']}")
    else:
        print("Write: OK")

    client.close()

    test_file = data_dir / "test_persistence.txt"
    if test_file.exists():
        print(f"✅ File persisted to: {test_file}")
        print(f"   Content: {test_file.read_text()}")
    else:
        print(f"❌ File not found at {test_file}")
        import os

        print(f"   Directory contents: {os.listdir(data_dir)}")


if __name__ == "__main__":
    test_filesystem_volume()
