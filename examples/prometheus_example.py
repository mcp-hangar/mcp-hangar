#!/usr/bin/env python3
"""Example: Using Prometheus MCP Server from pre-built image."""

import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_hangar.domain.model import Provider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main():
    """Demonstrate using Prometheus MCP server from pre-built image."""

    print("\n" + "=" * 70)
    print("📊 Prometheus MCP Server Example")
    print("=" * 70)
    print("\nThis example shows how to use a pre-built Docker image")
    print("from GitHub Container Registry without any Dockerfile.")
    print()

    # Configuration matching the user's example
    config = {
        "provider_id": "prometheus",
        "mode": "container",
        "image": "ghcr.io/pab1it0/prometheus-mcp-server:latest",
        "env": {"PROMETHEUS_URL": "http://localhost:9090"},  # Change to your Prometheus URL
        "network": "bridge",  # Needs network to connect to Prometheus
        "resources": {"memory": "256m", "cpu": "0.5"},
        "idle_ttl_s": 600,
    }

    print("📦 Configuration:")
    print(f"   Provider: {config['provider_id']}")
    print(f"   Image: {config['image']}")
    print(f"   Prometheus URL: {config['env']['PROMETHEUS_URL']}")
    print()

    try:
        # Create provider
        print("🔄 Creating provider from pre-built image...")
        provider = Provider(**config)

        # Start provider
        print("🚀 Starting provider...")
        provider.ensure_ready()
        print(f"   ✅ Provider started! State: {provider.state.value}")

        # List available tools
        print("\n🔍 Available Prometheus tools:")
        tools = list(provider.tools)
        for i, tool in enumerate(tools, 1):
            print(f"   {i}. {tool.name}")
            if tool.description:
                desc = tool.description[:70] + "..." if len(tool.description) > 70 else tool.description
                print(f"      {desc}")

        print("\n" + "=" * 70)
        print("✅ Prometheus MCP Server is ready!")
        print("=" * 70)
        print("\nYou can now invoke Prometheus queries through the registry:")
        print("  - Use registry_invoke with provider='prometheus'")
        print("  - Check available tools with registry_tools")
        print()

        # Keep provider alive for demo
        input("Press Enter to stop the provider...")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nTroubleshooting:")
        print("  1. Make sure Prometheus is running at the configured URL")
        print("  2. Check if the image is accessible: podman pull ghcr.io/pab1it0/prometheus-mcp-server:latest")
        print("  3. Verify network connectivity")
        return 1

    finally:
        print("\n🛑 Stopping provider...")
        try:
            provider.shutdown()
            print("   ✅ Provider stopped.")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
