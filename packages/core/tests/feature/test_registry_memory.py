#!/usr/bin/env python3
"""Quick test for memory provider via registry server.

This is a pytest test file.
- Do not return booleans from tests (use assertions).
- Use python3 executable for spawning the registry process.
- Skip when `config.container.yaml` is not available (repo/worktree dependent).
"""

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_registry_with_memory() -> None:
    """Test that registry can invoke memory provider successfully."""

    if not Path("config.container.yaml").exists():
        pytest.skip("config.container.yaml not present in repo/worktree")

    print("\n" + "=" * 70)
    print("🧪 Testing Registry with Memory Provider")
    print("=" * 70)

    # Start registry server in background
    print("\n🚀 Starting registry server...")
    env = {
        "MCP_CONFIG": "config.container.yaml",
        "PATH": subprocess.os.environ["PATH"],
    }

    proc = subprocess.Popen(
        ["python3", "-m", "mcp_hangar.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )

    # Wait for server to be ready
    print("   Waiting for server to initialize...")
    time.sleep(8)

    try:
        # Send initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        }
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()

        # Read response
        assert proc.stdout is not None
        response = proc.stdout.readline()
        assert response, "no response from mcp_hangar on initialize"
        init_resp = json.loads(response)
        assert "error" not in init_resp, f"registry initialize error: {init_resp.get('error')}"
        assert "result" in init_resp, f"registry initialize missing result: {init_resp}"
        print("   ✅ Registry initialized")

        # Invoke memory tool
        print("\n📝 Invoking memory provider: create_entities...")
        invoke_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "hangar_call",
                "arguments": {
                    "calls": [
                        {
                            "provider": "memory",
                            "tool": "create_entities",
                            "arguments": {
                                "entities": [
                                    {
                                        "name": "integration-test",
                                        "entityType": "test",
                                        "observations": ["Registry memory test successful"],
                                    }
                                ]
                            },
                        }
                    ],
                },
            },
        }
        proc.stdin.write(json.dumps(invoke_req) + "\n")
        proc.stdin.flush()

        response = proc.stdout.readline()
        assert response, "no response from mcp_hangar on create_entities"
        result = json.loads(response)

        assert "error" not in result, f"hangar_call returned error: {result.get('error')}"
        content = result.get("result", {}).get("content", [])
        assert content, f"hangar_call missing content: {result}"

        if "text" in content[0]:
            data = json.loads(content[0]["text"])
            assert not data.get("isError"), data.get("content", [{}])[0].get("text", "Unknown error")

        print("   ✅ SUCCESS! Entity created via registry")

        # Test read_graph
        print("\n📖 Reading graph...")
        read_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "hangar_call",
                "arguments": {
                    "calls": [
                        {
                            "provider": "memory",
                            "tool": "read_graph",
                            "arguments": {},
                        }
                    ],
                },
            },
        }
        proc.stdin.write(json.dumps(read_req) + "\n")
        proc.stdin.flush()

        response = proc.stdout.readline()
        assert response, "no response from mcp_hangar on read_graph"
        result = json.loads(response)

        assert "error" not in result, f"read_graph invoke error: {result.get('error')}"
        assert "result" in result, f"read_graph missing result: {result}"
        print("   ✅ Graph read successfully")

        print("\n" + "=" * 70)
        print("✅ Registry integration test PASSED!")
        print("=" * 70)

    finally:
        print("\n🛑 Stopping registry server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    # Keep script mode usable, but make it fail properly when assertions fail.
    try:
        test_registry_with_memory()
    except AssertionError:
        sys.exit(1)
    sys.exit(0)
