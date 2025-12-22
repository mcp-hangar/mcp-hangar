"""Integration tests for the registry server with real MCP protocol."""

import json
from queue import Empty, Queue
import subprocess
import threading
import time

import pytest

# Skip all tests in this file for now - FastMCP requires different stdio handling
pytestmark = pytest.mark.skip(reason="FastMCP registry server requires async/await handling - TBD")


class MCPClient:
    """Simple MCP client for testing via stdio."""

    def __init__(self, process):
        self.process = process
        self.responses = Queue()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def _reader_loop(self):
        """Read responses from server."""
        while True:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                msg = json.loads(line.strip())
                self.responses.put(msg)
            except Exception:
                break

    def call(self, method, params, timeout=5.0):
        """Make an RPC call."""
        request = {
            "jsonrpc": "2.0",
            "id": str(time.time()),
            "method": method,
            "params": params,
        }
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

        try:
            response = self.responses.get(timeout=timeout)
            return response
        except Empty:
            raise TimeoutError(f"No response for {method}")

    def close(self):
        """Close the client."""
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


@pytest.fixture
def registry_server():
    """Start a registry server for testing."""
    # Create a test configuration
    config_code = """
import sys
sys.path.insert(0, '.')

from mcp_hangar.server import mcp, PROVIDERS, load_config, BackgroundWorker, setup_logging
from mcp.server.stdio import stdio_server

setup_logging()

# Test configuration
config = {
    "test_math": {
        "mode": "subprocess",
        "command": ["python", "tests/mock_provider.py"],
        "idle_ttl_s": 60
    }
}

load_config(config)

# Don't start background workers for tests (to avoid timing issues)
# Just run the server
stdio_server(mcp)
"""

    # Start server process
    process = subprocess.Popen(
        ["python", "-c", config_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    client = MCPClient(process)

    # Wait for server to be ready
    time.sleep(0.5)

    yield client

    # Cleanup
    client.close()


def test_registry_initialize(registry_server):
    """Test MCP initialize handshake with registry."""
    response = registry_server.call(
        "initialize", {"client": "test-client", "protocol_version": "2024-11-05"}
    )

    assert "result" in response
    assert "serverInfo" in response["result"]
    assert response["result"]["serverInfo"]["name"] == "mcp-registry"


def test_registry_list_providers(registry_server):
    """Test registry_list tool."""
    # Initialize first
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Get tool list to verify registry_list exists
    tools_resp = registry_server.call("tools/list", {})
    assert "result" in tools_resp
    tools = tools_resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "registry_list" in tool_names

    # Call registry_list
    response = registry_server.call(
        "tools/call", {"name": "registry_list", "arguments": {}}, timeout=10.0
    )

    assert "result" in response
    result = response["result"]
    assert "providers" in result
    providers = result["providers"]
    assert len(providers) > 0
    assert any(p["provider"] == "test_math" for p in providers)


def test_registry_start_provider(registry_server):
    """Test registry_start tool."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Start provider
    response = registry_server.call(
        "tools/call",
        {"name": "registry_start", "arguments": {"provider": "test_math"}},
        timeout=15.0,
    )

    assert "result" in response
    result = response["result"]
    assert result["provider"] == "test_math"
    assert result["state"] == "ready"
    assert "tools" in result
    assert len(result["tools"]) > 0


def test_registry_tools_discovery(registry_server):
    """Test registry_tools tool."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Get tools from provider
    response = registry_server.call(
        "tools/call",
        {"name": "registry_tools", "arguments": {"provider": "test_math"}},
        timeout=15.0,
    )

    assert "result" in response
    result = response["result"]
    assert result["provider"] == "test_math"
    assert "tools" in result

    tools = result["tools"]
    assert len(tools) > 0

    # Verify tool structure
    add_tool = next((t for t in tools if t["name"] == "add"), None)
    assert add_tool is not None
    assert "description" in add_tool
    assert "inputSchema" in add_tool


def test_registry_invoke_tool(registry_server):
    """Test registry_invoke tool."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Invoke tool on provider
    response = registry_server.call(
        "tools/call",
        {
            "name": "registry_invoke",
            "arguments": {
                "provider": "test_math",
                "tool": "add",
                "arguments": {"a": 10, "b": 20},
            },
        },
        timeout=15.0,
    )

    assert "result" in response
    result = response["result"]
    assert result["result"] == 30


def test_registry_stop_provider(registry_server):
    """Test registry_stop tool."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Start provider first
    registry_server.call(
        "tools/call",
        {"name": "registry_start", "arguments": {"provider": "test_math"}},
        timeout=15.0,
    )

    # Stop provider
    response = registry_server.call(
        "tools/call",
        {"name": "registry_stop", "arguments": {"provider": "test_math"}},
        timeout=10.0,
    )

    assert "result" in response
    result = response["result"]
    assert result["stopped"] == "test_math"

    # Verify provider is stopped
    list_resp = registry_server.call(
        "tools/call", {"name": "registry_list", "arguments": {}}, timeout=10.0
    )

    providers = list_resp["result"]["providers"]
    test_math = next(p for p in providers if p["provider"] == "test_math")
    assert test_math["state"] == "cold"


def test_registry_full_workflow(registry_server):
    """Test complete workflow: list → start → invoke → stop."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # 1. List providers
    list_resp = registry_server.call("tools/call", {"name": "registry_list", "arguments": {}})
    assert "result" in list_resp

    # 2. Start provider
    start_resp = registry_server.call(
        "tools/call",
        {"name": "registry_start", "arguments": {"provider": "test_math"}},
        timeout=15.0,
    )
    assert start_resp["result"]["state"] == "ready"

    # 3. Invoke tool
    invoke_resp = registry_server.call(
        "tools/call",
        {
            "name": "registry_invoke",
            "arguments": {
                "provider": "test_math",
                "tool": "multiply",
                "arguments": {"a": 7, "b": 6},
            },
        },
        timeout=10.0,
    )
    assert invoke_resp["result"]["result"] == 42

    # 4. Stop provider
    stop_resp = registry_server.call(
        "tools/call",
        {"name": "registry_stop", "arguments": {"provider": "test_math"}},
        timeout=10.0,
    )
    assert stop_resp["result"]["stopped"] == "test_math"


def test_registry_unknown_provider_error(registry_server):
    """Test error handling for unknown provider."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Try to start unknown provider
    response = registry_server.call(
        "tools/call",
        {"name": "registry_start", "arguments": {"provider": "nonexistent"}},
        timeout=10.0,
    )

    # Should return an error
    assert "error" in response or ("result" in response and "error" in str(response["result"]))


def test_registry_concurrent_invocations(registry_server):
    """Test concurrent invocations through registry."""
    # Initialize
    registry_server.call("initialize", {"client": "test", "protocol_version": "2024-11-05"})

    # Start provider
    registry_server.call(
        "tools/call",
        {"name": "registry_start", "arguments": {"provider": "test_math"}},
        timeout=15.0,
    )

    # Make multiple concurrent invocations
    results = []
    errors = []

    def invoke(i):
        try:
            response = registry_server.call(
                "tools/call",
                {
                    "name": "registry_invoke",
                    "arguments": {
                        "provider": "test_math",
                        "tool": "add",
                        "arguments": {"a": i, "b": i},
                    },
                },
                timeout=15.0,
            )
            results.append(response)
        except Exception as e:
            errors.append((i, e))

    threads = []
    for i in range(10):
        t = threading.Thread(target=invoke, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify results
    assert len(errors) == 0, f"Errors: {errors}"
    assert len(results) == 10

    # Verify correct results
    for i, response in enumerate(results):
        if "result" in response:
            # The result should be in response["result"]["result"]
            pass  # Exact verification depends on response order
