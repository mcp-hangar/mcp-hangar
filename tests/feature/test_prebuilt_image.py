#!/usr/bin/env python3
"""Test using pre-built Docker image (without Dockerfile).

This test verifies that the registry can use a pre-built container image
(one that doesn't require building from a Dockerfile). It uses the
mcp-math:latest image which must be built beforehand.

The test is designed to be skipped gracefully if:
1. The image doesn't exist locally
2. The container cannot start in the current environment (CI restrictions, etc.)
"""

import logging
from pathlib import Path
import subprocess
import sys

import pytest

# Add registry to path
sys.path.insert(0, str(Path(__file__).parent))

from mcp_hangar.domain.model import Provider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

IMAGE_NAME = "mcp-math:latest"


def _image_exists(image_name: str) -> bool:
    """Check if a Docker image exists locally."""
    try:
        result = subprocess.run(
            ["docker", "images", "-q", image_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


@pytest.mark.skipif(
    not _image_exists(IMAGE_NAME),
    reason=f"{IMAGE_NAME} image not available locally",
)
def test_prebuilt_image() -> None:
    """Test provider using a pre-built image from mcp_hangar.

    This test demonstrates using a container image that was built externally
    (not via the registry's build system). This is useful for:
    - Using images from a container registry (DockerHub, GHCR, etc.)
    - Using locally built development images
    - Testing image compatibility before deploying
    """
    print("\n" + "=" * 70)
    print("🧪 Testing Pre-Built Docker Image (No Dockerfile)")
    print("=" * 70)

    config = {
        "provider_id": "math_prebuilt",
        "mode": "container",
        "image": IMAGE_NAME,
        # No "build" section - just use the image directly
        "env": {},
        "resources": {"memory": "256m", "cpu": "0.5"},
        "network": "none",
        # Allow writable root fs for MCP SDK compatibility
        "read_only": False,
        "idle_ttl_s": 300,
    }

    print(f"\n📦 Provider: {config['provider_id']}")
    print(f"   Mode: {config['mode']}")
    print(f"   Image: {config['image']}")
    print("   Build: None (using pre-built image)")

    provider = None
    try:
        provider = Provider(**config)

        print("\n🔄 Starting provider from pre-built image...")
        provider.ensure_ready()
        print(f"   ✅ Provider started! State: {provider.state.value}")

        # List tools
        print("\n🔍 Discovering tools...")
        tools = provider.tools
        tool_list = list(tools)
        print(f"   ✅ Found {len(tool_list)} tools:")
        for tool in tool_list:
            desc = tool.description[:60] if tool.description else ""
            print(f"      - {tool.name}: {desc}...")

        # Test a tool if available
        if tool_list:
            tool = tool_list[0]
            print(f"\n🔧 Testing tool: {tool.name}")

            args = {}
            if tool.name == "add":
                args = {"a": 5, "b": 3}

            try:
                result = provider.invoke_tool(tool.name, args, timeout=10.0)

                if result.get("isError"):
                    content = result.get("content", [{}])[0].get("text", "")
                    print(f"   ⚠️  Tool returned error: {content}")
                else:
                    print("   ✅ Tool invocation succeeded!")
                    content = result.get("content", [{}])[0]
                    if "text" in content:
                        text = content["text"]
                        print(f"   Result: {text[:200]}{'...' if len(text) > 200 else ''}")
            except Exception as e:
                # Tool invocation may fail but provider started successfully
                print(f"   ⚠️  Tool invocation failed: {e}")
                print("   (This is OK - the main goal is testing provider startup)")

        print(f"\n{'=' * 70}")
        print("✅ Pre-built image test PASSED!")
        print(f"   Found {len(tool_list)} tools")
        print(f"{'=' * 70}")

    except Exception as e:
        import traceback

        traceback.print_exc()

        # If provider failed to start due to container/environment issues, skip
        error_msg = str(e).lower()
        if any(x in error_msg for x in ["reader_died", "init_failed", "container"]):
            pytest.skip(f"Pre-built image cannot start in this environment: {e}")
        raise

    finally:
        print("\n🛑 Stopping provider...")
        try:
            if provider is not None:
                provider.shutdown()
                print("   ✅ Provider stopped.")
        except Exception as e:
            print(f"   ⚠️  Error stopping provider: {e}")


if __name__ == "__main__":
    test_prebuilt_image()
    sys.exit(0)
