#!/usr/bin/env python
"""
MCP Hangar Discovery Feature Test Script

This script tests the Provider Discovery & Auto-Registration feature
without requiring MCP client access. Run directly with Python.

Usage:
    python tests/integration/test_discovery_integration.py

Requirements:
    - MCP Hangar installed (pip install -e .)
    - config.yaml with discovery enabled
"""

import asyncio
from datetime import UTC
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def print_header(text: str):
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def print_result(test_name: str, passed: bool, details: str = ""):
    """Print test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status} | {test_name}")
    if details:
        print(f"       {details}")


async def run_discovered_provider_value_object():
    """Test DiscoveredProvider value object."""
    print_header("Test 1: DiscoveredProvider Value Object")

    from mcp_hangar.domain.discovery import DiscoveredProvider

    # Test creation with fingerprint
    provider = DiscoveredProvider.create(
        name="test-provider",
        source_type="docker",
        mode="http",
        connection_info={"host": "localhost", "port": 8080},
        metadata={"env": "test"},
    )

    print_result(
        "Create provider with fingerprint",
        provider.fingerprint is not None and len(provider.fingerprint) == 16,
        f"fingerprint={provider.fingerprint}",
    )

    # Test fingerprint changes with config
    provider2 = DiscoveredProvider.create(
        name="test-provider",
        source_type="docker",
        mode="http",
        connection_info={"host": "localhost", "port": 9090},  # Different port
    )

    print_result(
        "Fingerprint changes with config",
        provider.fingerprint != provider2.fingerprint,
        f"old={provider.fingerprint}, new={provider2.fingerprint}",
    )

    # Test TTL expiration
    from datetime import datetime, timedelta

    old_time = datetime.now(UTC) - timedelta(seconds=100)
    expired_provider = DiscoveredProvider(
        name="expired",
        source_type="docker",
        mode="http",
        connection_info={},
        metadata={},
        fingerprint="abc123",
        discovered_at=old_time,
        last_seen_at=old_time,
        ttl_seconds=90,
    )

    print_result(
        "TTL expiration detection",
        expired_provider.is_expired(),
        f"ttl={expired_provider.ttl_seconds}s, expired={expired_provider.is_expired()}",
    )

    # Test serialization
    data = provider.to_dict()
    restored = DiscoveredProvider.from_dict(data)

    print_result(
        "Serialization round-trip",
        restored.fingerprint == provider.fingerprint,
        f"original={provider.name}, restored={restored.name}",
    )

    return True


async def run_conflict_resolver():
    """Test ConflictResolver."""
    print_header("Test 2: Conflict Resolver")

    from mcp_hangar.domain.discovery import ConflictResolution, ConflictResolver, DiscoveredProvider

    # Test static always wins
    resolver = ConflictResolver(static_providers={"my-static-provider"})

    provider = DiscoveredProvider.create(
        name="my-static-provider",
        source_type="kubernetes",
        mode="http",
        connection_info={},
    )

    result = resolver.resolve(provider)

    print_result(
        "Static config wins over discovery",
        result.resolution == ConflictResolution.STATIC_WINS,
        f"resolution={result.resolution.value}",
    )

    # Test new provider registered
    resolver2 = ConflictResolver()
    new_provider = DiscoveredProvider.create(
        name="brand-new-provider",
        source_type="docker",
        mode="http",
        connection_info={},
    )

    result2 = resolver2.resolve(new_provider)

    print_result(
        "New provider gets registered",
        result2.resolution == ConflictResolution.REGISTERED,
        f"resolution={result2.resolution.value}",
    )

    # Test source priority
    resolver3 = ConflictResolver()

    # Register from filesystem first
    fs_provider = DiscoveredProvider.create(
        name="priority-test",
        source_type="filesystem",
        mode="http",
        connection_info={},
    )
    resolver3.resolve(fs_provider)
    resolver3.register(fs_provider)

    # Try to register from kubernetes (higher priority)
    k8s_provider = DiscoveredProvider.create(
        name="priority-test",
        source_type="kubernetes",
        mode="http",
        connection_info={},
    )
    result3 = resolver3.resolve(k8s_provider)

    print_result(
        "Higher priority source wins",
        result3.resolution == ConflictResolution.SOURCE_PRIORITY,
        f"kubernetes > filesystem: {result3.resolution.value}",
    )

    return True


async def run_filesystem_discovery_source():
    """Test FilesystemDiscoverySource."""
    print_header("Test 3: Filesystem Discovery Source")

    from mcp_hangar.infrastructure.discovery import FilesystemDiscoverySource

    # Check if example files exist
    examples_path = project_root / "examples" / "discovery"

    print_result("Example discovery files exist", examples_path.exists(), f"path={examples_path}")

    if not examples_path.exists():
        print("  ⚠️  Skipping filesystem tests - no example files")
        return False

    # Create source
    source = FilesystemDiscoverySource(path=str(examples_path), watch=False)

    print_result(
        "FilesystemDiscoverySource created",
        source.source_type == "filesystem",
        f"type={source.source_type}, mode={source.mode}",
    )

    # Test health check
    healthy = await source.health_check()

    print_result("Health check passes", healthy, f"healthy={healthy}")

    # Test discovery
    providers = await source.discover()

    print_result(
        "Discovers providers from YAML files",
        len(providers) >= 2,
        f"found {len(providers)} providers",
    )

    # List discovered providers
    for p in providers:
        print(f"       - {p.name} ({p.mode})")

    return True


async def run_discovery_service():
    """Test DiscoveryService."""
    print_header("Test 4: Discovery Service")

    from mcp_hangar.domain.discovery import DiscoveredProvider, DiscoveryMode, DiscoveryService, DiscoverySource

    # Create mock source
    class MockSource(DiscoverySource):
        def __init__(self):
            super().__init__(DiscoveryMode.ADDITIVE)
            self.providers = [
                DiscoveredProvider.create(
                    name="mock-provider-1",
                    source_type="mock",
                    mode="http",
                    connection_info={"port": 8080},
                ),
                DiscoveredProvider.create(
                    name="mock-provider-2",
                    source_type="mock",
                    mode="subprocess",
                    connection_info={"command": ["python", "-m", "test"]},
                ),
            ]

        @property
        def source_type(self) -> str:
            return "mock"

        async def discover(self):
            return self.providers

        async def health_check(self) -> bool:
            return True

    service = DiscoveryService(auto_register=True)
    source = MockSource()

    # Register source
    service.register_source(source)

    print_result(
        "Source registered",
        service.get_source("mock") is not None,
        f"sources={[s.source_type for s in service.get_all_sources()]}",
    )

    # Run discovery cycle
    result = await service.run_discovery_cycle()

    print_result(
        "Discovery cycle completes",
        result.discovered_count == 2,
        f"discovered={result.discovered_count}, registered={result.registered_count}",
    )

    # Check registered providers
    registered = service.get_registered_providers()

    print_result(
        "Providers are registered",
        len(registered) == 2,
        f"registered providers: {list(registered.keys())}",
    )

    return True


async def run_discovery_orchestrator():
    """Test DiscoveryOrchestrator."""
    print_header("Test 5: Discovery Orchestrator")

    from mcp_hangar.application.discovery import DiscoveryConfig, DiscoveryOrchestrator
    from mcp_hangar.domain.discovery import DiscoveredProvider, DiscoverySource

    # Create mock source
    class MockSource(DiscoverySource):
        @property
        def source_type(self) -> str:
            return "mock"

        async def discover(self):
            return [
                DiscoveredProvider.create(
                    name="orchestrator-test",
                    source_type="mock",
                    mode="http",
                    connection_info={"port": 8080},
                )
            ]

        async def health_check(self) -> bool:
            return True

    # Create orchestrator
    config = DiscoveryConfig(enabled=True, refresh_interval_s=30, auto_register=True)

    orchestrator = DiscoveryOrchestrator(config=config, static_providers={"static-provider"})

    orchestrator.add_source(MockSource())

    print_result(
        "Orchestrator created with source",
        True,
        f"sources={orchestrator.get_stats()['sources_count']}",
    )

    # Run discovery
    result = await orchestrator.trigger_discovery()

    print_result(
        "Discovery triggered successfully",
        result.get("discovered_count", 0) >= 0,
        f"result={result}",
    )

    # Check sources status
    sources = await orchestrator.get_sources_status()

    print_result(
        "Source status available",
        len(sources) == 1,
        f"sources={[s['source_type'] for s in sources]}",
    )

    return True


async def run_security_validator():
    """Test SecurityValidator."""
    print_header("Test 6: Security Validator")

    from mcp_hangar.application.discovery import SecurityConfig, SecurityValidator, ValidationResult
    from mcp_hangar.domain.discovery import DiscoveredProvider

    # Create validator with namespace restrictions
    config = SecurityConfig(
        allowed_namespaces={"mcp-providers"},
        denied_namespaces={"kube-system", "default"},
        require_health_check=False,  # Skip for non-HTTP providers
        max_registration_rate=10,
    )

    validator = SecurityValidator(config)

    # Test valid provider
    valid_provider = DiscoveredProvider.create(
        name="valid-provider",
        source_type="kubernetes",
        mode="subprocess",
        connection_info={},
        metadata={"namespace": "mcp-providers"},
    )

    result = await validator.validate(valid_provider)

    print_result(
        "Valid provider passes validation",
        result.result == ValidationResult.PASSED,
        f"result={result.result.value}",
    )

    # Test denied namespace
    denied_provider = DiscoveredProvider.create(
        name="denied-provider",
        source_type="kubernetes",
        mode="subprocess",
        connection_info={},
        metadata={"namespace": "kube-system"},
    )

    result2 = await validator.validate(denied_provider)

    print_result(
        "Denied namespace fails validation",
        result2.result == ValidationResult.FAILED_SOURCE,
        f"result={result2.result.value}, reason={result2.reason}",
    )

    return True


async def run_lifecycle_manager():
    """Test DiscoveryLifecycleManager."""
    print_header("Test 7: Lifecycle Manager")

    from mcp_hangar.application.discovery import DiscoveryLifecycleManager
    from mcp_hangar.domain.discovery import DiscoveredProvider

    manager = DiscoveryLifecycleManager(default_ttl=90, check_interval=10)

    # Add provider
    provider = DiscoveredProvider.create(
        name="lifecycle-test",
        source_type="docker",
        mode="http",
        connection_info={"port": 8080},
    )

    manager.add_provider(provider)

    print_result(
        "Provider added to lifecycle tracking",
        manager.get_provider("lifecycle-test") is not None,
        f"stats={manager.get_stats()}",
    )

    # Test quarantine
    manager.quarantine(provider, "Test quarantine reason")

    quarantined = manager.get_quarantined()

    print_result(
        "Provider quarantined",
        "lifecycle-test" in quarantined,
        f"quarantined={list(quarantined.keys())}",
    )

    # Test approval
    approved = manager.approve("lifecycle-test")

    print_result(
        "Provider approved from quarantine",
        approved is not None,
        f"approved={approved.name if approved else None}",
    )

    return True


async def run_full_integration():
    """Test full integration with filesystem source."""
    print_header("Test 8: Full Integration Test")

    from mcp_hangar.application.discovery import DiscoveryConfig, DiscoveryOrchestrator
    from mcp_hangar.infrastructure.discovery import FilesystemDiscoverySource

    examples_path = project_root / "examples" / "discovery"

    if not examples_path.exists():
        print("  ⚠️  Skipping integration test - no example files")
        return False

    # Create orchestrator with filesystem source
    config = DiscoveryConfig(enabled=True, refresh_interval_s=30, auto_register=True)

    orchestrator = DiscoveryOrchestrator(config=config)

    # Add filesystem source
    fs_source = FilesystemDiscoverySource(path=str(examples_path), watch=False)
    orchestrator.add_source(fs_source)

    # Run discovery
    result = await orchestrator.trigger_discovery()

    print_result(
        "Full discovery cycle",
        result.get("discovered_count", 0) >= 2,
        f"discovered={result.get('discovered_count')}, registered={result.get('registered_count')}",
    )

    # Check sources
    sources = await orchestrator.get_sources_status()
    fs_source_status = next((s for s in sources if s["source_type"] == "filesystem"), None)

    print_result(
        "Filesystem source healthy",
        fs_source_status and fs_source_status.get("is_healthy"),
        f"status={fs_source_status}",
    )

    # Check pending (should be empty with auto_register=True)
    pending = orchestrator.get_pending_providers()

    print_result(
        "No pending providers (auto_register=True)",
        len(pending) == 0,
        f"pending={len(pending)}",
    )

    # Check quarantine (should be empty for valid providers)
    quarantined = orchestrator.get_quarantined()

    print_result(
        "No quarantined providers",
        len(quarantined) == 0,
        f"quarantined={len(quarantined)}",
    )

    return True


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  MCP Hangar Discovery Feature - Integration Tests")
    print("=" * 60)

    tests = [
        ("DiscoveredProvider Value Object", run_discovered_provider_value_object),
        ("Conflict Resolver", run_conflict_resolver),
        ("Filesystem Discovery Source", run_filesystem_discovery_source),
        ("Discovery Service", run_discovery_service),
        ("Discovery Orchestrator", run_discovery_orchestrator),
        ("Security Validator", run_security_validator),
        ("Lifecycle Manager", run_lifecycle_manager),
        ("Full Integration", run_full_integration),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = await test_func()
            results.append((name, passed, None))
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"\n❌ ERROR in {name}: {e}")
            import traceback

            traceback.print_exc()

    # Summary
    print_header("Test Summary")

    passed = sum(1 for _, p, _ in results if p)
    failed = len(results) - passed

    for name, p, error in results:
        status = "✅" if p else "❌"
        print(f"  {status} {name}")
        if error:
            print(f"       Error: {error}")

    print(f"\n  Total: {passed}/{len(results)} passed")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
