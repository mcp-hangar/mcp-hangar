#!/usr/bin/env python3
"""
Container Discovery Test Suite

Tests Docker/Podman discovery with different scenarios.
Works with both Docker and Podman (auto-detected).

Usage:
    # Run all tests
    python test_container_discovery.py

    # Run specific test
    python test_container_discovery.py --test container_mode

    # Use specific socket
    python test_container_discovery.py --socket /path/to/socket
"""

import argparse
import asyncio
from pathlib import Path
import subprocess
import sys

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class ContainerTestRunner:
    """Run container discovery tests."""

    def __init__(self, socket_path: str = None, runtime: str = "podman"):
        self.socket_path = socket_path
        self.runtime = runtime  # "podman" or "docker"
        self.containers_created = []

    def run_cmd(self, *args) -> tuple[bool, str]:
        """Run container command."""
        cmd = [self.runtime] + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)

    def create_container(self, name: str, image: str, labels: dict, **kwargs) -> bool:
        """Create a container with labels."""
        cmd = ["run", "-d", "--name", name]

        for key, value in labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        cmd.append(image)

        if "command" in kwargs:
            cmd.extend(kwargs["command"])

        success, output = self.run_cmd(*cmd)
        if success:
            self.containers_created.append(name)
            print(f"   ✅ Created container: {name}")
        else:
            print(f"   ❌ Failed to create {name}: {output}")
        return success

    def cleanup(self):
        """Remove test containers."""
        for name in self.containers_created:
            self.run_cmd("stop", name)
            self.run_cmd("rm", name)
        self.containers_created.clear()

    async def test_discovery(self) -> tuple[bool, list]:
        """Run discovery and return results."""
        from mcp_hangar.infrastructure.discovery import DockerDiscoverySource

        source = DockerDiscoverySource(socket_path=self.socket_path)

        healthy = await source.health_check()
        if not healthy:
            return False, []

        providers = await source.discover()
        return True, providers


async def test_socket_detection():
    """Test 1: Socket auto-detection."""
    print("\n" + "=" * 60)
    print("TEST 1: Socket Auto-Detection")
    print("=" * 60)

    from mcp_hangar.infrastructure.discovery.docker_source import find_container_socket

    socket = find_container_socket()

    if socket:
        print(f"   ✅ Found socket: {socket}")
        return True
    else:
        print("   ❌ No socket found")
        print("   Make sure Docker or Podman is running")
        return False


async def test_container_mode(runner: ContainerTestRunner):
    """Test 2: Container mode discovery."""
    print("\n" + "=" * 60)
    print("TEST 2: Container Mode Discovery")
    print("=" * 60)

    # Create container with container mode labels
    success = runner.create_container(
        name="mcp-test-container-mode",
        image="docker.io/library/alpine:latest",
        labels={
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "test-container-provider",
            "mcp.hangar.mode": "container",
        },
        command=["sleep", "10"],
    )

    if not success:
        return False

    # Run discovery
    ok, providers = await runner.test_discovery()
    if not ok:
        print("   ❌ Discovery failed")
        return False

    # Check results
    found = [p for p in providers if p.name == "test-container-provider"]
    if found:
        p = found[0]
        print(f"   ✅ Found provider: {p.name}")
        print(f"      Mode: {p.mode}")
        print(f"      Image: {p.connection_info.get('image')}")
        print(f"      Status: {p.metadata.get('status')}")
        return p.mode == "container" and p.connection_info.get("image")
    else:
        print("   ❌ Provider not found in discovery")
        return False


async def test_http_mode(runner: ContainerTestRunner):
    """Test 3: HTTP mode discovery (requires running container with network)."""
    print("\n" + "=" * 60)
    print("TEST 3: HTTP Mode Discovery")
    print("=" * 60)

    # Create container with HTTP mode labels
    success = runner.create_container(
        name="mcp-test-http-mode",
        image="docker.io/library/python:3.11-slim",
        labels={
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "test-http-provider",
            "mcp.hangar.mode": "http",
            "mcp.hangar.port": "8080",
        },
        command=["python", "-m", "http.server", "8080"],
    )

    if not success:
        return False

    # Wait for container to get IP
    await asyncio.sleep(2)

    # Run discovery
    ok, providers = await runner.test_discovery()
    if not ok:
        print("   ❌ Discovery failed")
        return False

    # Check results
    found = [p for p in providers if p.name == "test-http-provider"]
    if found:
        p = found[0]
        print(f"   ✅ Found provider: {p.name}")
        print(f"      Mode: {p.mode}")
        print(f"      Host: {p.connection_info.get('host')}")
        print(f"      Port: {p.connection_info.get('port')}")
        print(f"      Endpoint: {p.connection_info.get('endpoint')}")
        return p.mode == "http" and p.connection_info.get("host")
    else:
        print("   ⚠️  Provider not found (may lack IP address)")
        return False


async def test_custom_labels(runner: ContainerTestRunner):
    """Test 4: Custom labels (command, volumes, group)."""
    print("\n" + "=" * 60)
    print("TEST 4: Custom Labels")
    print("=" * 60)

    # Create container with custom labels
    success = runner.create_container(
        name="mcp-test-custom",
        image="docker.io/library/alpine:latest",
        labels={
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "test-custom-provider",
            "mcp.hangar.mode": "container",
            "mcp.hangar.group": "test-group",
            "mcp.hangar.command": "python -m myapp",
            "mcp.hangar.volumes": "/data:/data,/config:/config",
            "mcp.hangar.ttl": "120",
        },
        command=["sleep", "10"],
    )

    if not success:
        return False

    # Run discovery
    ok, providers = await runner.test_discovery()
    if not ok:
        return False

    # Check results
    found = [p for p in providers if p.name == "test-custom-provider"]
    if found:
        p = found[0]
        print(f"   ✅ Found provider: {p.name}")
        print(f"      Group: {p.metadata.get('group')}")
        print(f"      Command: {p.connection_info.get('command')}")
        print(f"      Volumes: {p.connection_info.get('volumes')}")
        print(f"      TTL: {p.ttl_seconds}s")

        return (
            p.metadata.get("group") == "test-group"
            and p.connection_info.get("command") == ["python", "-m", "myapp"]
            and p.connection_info.get("volumes") == ["/data:/data", "/config:/config"]
            and p.ttl_seconds == 120
        )
    return False


async def test_stopped_container(runner: ContainerTestRunner):
    """Test 5: Stopped container is still discovered."""
    print("\n" + "=" * 60)
    print("TEST 5: Stopped Container Discovery")
    print("=" * 60)

    # Create and stop container
    success = runner.create_container(
        name="mcp-test-stopped",
        image="docker.io/library/alpine:latest",
        labels={
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "test-stopped-provider",
            "mcp.hangar.mode": "container",
        },
        command=["echo", "done"],  # Exits immediately
    )

    if not success:
        return False

    # Wait for container to exit
    await asyncio.sleep(2)

    # Run discovery
    ok, providers = await runner.test_discovery()
    if not ok:
        return False

    # Check results
    found = [p for p in providers if p.name == "test-stopped-provider"]
    if found:
        p = found[0]
        print(f"   ✅ Found stopped container: {p.name}")
        print(f"      Status: {p.metadata.get('status')}")
        return p.metadata.get("status") == "exited"
    return False


async def test_no_label_ignored(runner: ContainerTestRunner):
    """Test 6: Container without enabled label is ignored."""
    print("\n" + "=" * 60)
    print("TEST 6: Container Without Label Ignored")
    print("=" * 60)

    # Create container WITHOUT mcp.hangar.enabled label
    cmd = ["run", "-d", "--name", "mcp-test-no-label", "alpine:latest", "sleep", "10"]
    success, _ = runner.run_cmd(*cmd)
    if success:
        runner.containers_created.append("mcp-test-no-label")

    # Run discovery
    ok, providers = await runner.test_discovery()
    if not ok:
        return False

    # Check results - should NOT find this container
    found = [p for p in providers if "no-label" in p.name]
    if not found:
        print("   ✅ Container without label correctly ignored")
        return True
    else:
        print("   ❌ Container without label was incorrectly discovered")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Container Discovery Tests")
    parser.add_argument("--socket", "-s", help="Socket path")
    parser.add_argument("--runtime", "-r", default="podman", choices=["podman", "docker"], help="Container runtime")
    parser.add_argument("--test", "-t", help="Run specific test")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Container Discovery Test Suite")
    print("  Runtime:", args.runtime)
    print("=" * 60)

    runner = ContainerTestRunner(socket_path=args.socket, runtime=args.runtime)

    tests = [
        ("socket_detection", test_socket_detection),
        ("container_mode", lambda: test_container_mode(runner)),
        ("http_mode", lambda: test_http_mode(runner)),
        ("custom_labels", lambda: test_custom_labels(runner)),
        ("stopped_container", lambda: test_stopped_container(runner)),
        ("no_label_ignored", lambda: test_no_label_ignored(runner)),
    ]

    results = []

    try:
        for name, test_fn in tests:
            if args.test and args.test != name:
                continue

            try:
                if asyncio.iscoroutinefunction(test_fn):
                    passed = await test_fn()
                else:
                    passed = await test_fn()
                results.append((name, passed))
            except Exception as e:
                print(f"   ❌ Error: {e}")
                results.append((name, False))
    finally:
        print("\n🧹 Cleaning up test containers...")
        runner.cleanup()

    # Summary
    print("\n" + "=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, p in results if p)
    total = len(results)

    for name, p in results:
        status = "✅ PASS" if p else "❌ FAIL"
        print(f"   {status}  {name}")

    print(f"\n   Total: {passed}/{total} passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
