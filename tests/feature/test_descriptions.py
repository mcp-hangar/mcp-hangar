#!/usr/bin/env python3
"""Test that provider descriptions are properly returned in hangar_list.

This feature test depends on `config.container.yaml` being present in the worktree.
If the file is missing, the test should be skipped (not failed).
"""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from mcp_hangar.application.queries import register_all_handlers as register_query_handlers  # noqa: E402
from mcp_hangar.server import (  # noqa: E402
    hangar_list,
    load_config,
    load_config_from_file,
    PROVIDER_REPOSITORY,
    PROVIDERS,
    QUERY_BUS,
)


def test_descriptions() -> None:
    """Test that descriptions are loaded and returned."""
    print("\n" + "=" * 70)
    print("🧪 Testing Provider Descriptions")
    print("=" * 70)

    # Register query handlers
    print("\n🔧 Registering query handlers...")
    register_query_handlers(QUERY_BUS, PROVIDER_REPOSITORY)
    print("   ✅ Query handlers registered")

    # Load config
    print("\n📂 Loading config.container.yaml...")
    try:
        config = load_config_from_file("config.container.yaml")
    except FileNotFoundError:
        pytest.skip("config.container.yaml not present in repo/worktree")

    provider_config = config.get("providers", {})
    load_config(provider_config)
    print(f"   ✅ Loaded {len(PROVIDERS.keys())} providers")

    # Check descriptions in config
    print("\n🔍 Checking descriptions in config...")
    missing = []
    for provider_id, spec in provider_config.items():
        description = spec.get("description")
        if description:
            desc_preview = description.strip()[:60] + "..." if len(description.strip()) > 60 else description.strip()
            print(f"   ✅ {provider_id:20s} {desc_preview}")
        else:
            print(f"   ❌ {provider_id:20s} Missing description!")
            missing.append(provider_id)

    if missing:
        print(f"\n❌ {len(missing)} providers missing descriptions: {missing}")
        assert False, f"Providers missing descriptions: {missing}"

    # Test hangar_list returns descriptions
    print("\n📋 Testing hangar_list response...")
    try:
        result = hangar_list()
        providers_list = result.get("providers", [])

        print(f"   Found {len(providers_list)} providers in response:")

        has_description = 0
        no_description = []

        for provider in providers_list:
            provider_id = provider.get("provider")
            description = provider.get("description")

            if description:
                has_description += 1
                desc_preview = description[:50] + "..." if len(description) > 50 else description
                print(f"   ✅ {provider_id:20s} {desc_preview}")
            else:
                no_description.append(provider_id)
                print(f"   ❌ {provider_id:20s} No description in response")

        print("\n📊 Summary:")
        print(f"   Providers with descriptions: {has_description}/{len(providers_list)}")

        if no_description:
            print(f"   ❌ Missing descriptions: {no_description}")
            assert False, f"Missing descriptions in hangar_list response: {no_description}"

        print("\n✅ All providers have descriptions!")

        # Show example JSON
        if providers_list:
            print(f"\n📝 Example response for '{providers_list[0]['provider']}':")
            print(f"   {json.dumps(providers_list[0], indent=2)[:300]}...")

        return None

    except Exception as e:
        print(f"   ❌ Error calling hangar_list: {e}")
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🚀 Provider Description Feature Test")
    print("=" * 70)

    try:
        test_descriptions()
        success = True
    except Exception:
        success = False

    print("\n" + "=" * 70)
    if success:
        print("✅ ALL TESTS PASSED!")
        print("=" * 70)
        print("\n💡 AI models will now receive provider descriptions in hangar_list!")
        print("   This helps them understand what each provider can do.")
    else:
        print("❌ TESTS FAILED!")
        print("=" * 70)

    sys.exit(0 if success else 1)
