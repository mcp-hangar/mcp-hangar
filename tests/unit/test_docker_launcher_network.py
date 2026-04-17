"""Unit tests for capabilities-aware network mode in DockerLauncher.

Docker mode provides binary enforcement (deny-all or allow-all network) unlike K8s
which can restrict to specific CIDRs. This is a known limitation: Docker does not
support per-destination egress filtering via --network flags alone.

When capabilities are provided, they override the legacy enable_network constructor flag.
When no capabilities are provided, the existing enable_network behavior is preserved.
"""

from mcp_hangar.infrastructure.launchers.docker import DockerLauncher
from mcp_hangar.domain.value_objects.capabilities import (
    EgressRule,
    NetworkCapabilities,
    ProviderCapabilities,
)


class TestDockerLauncherNetworkMode:
    """Test capabilities-driven network mode selection in DockerLauncher."""

    def _make_launcher(self, enable_network: bool = False) -> DockerLauncher:
        """Create a DockerLauncher with explicit runtime to skip auto-detection."""
        return DockerLauncher(runtime="docker", enable_network=enable_network)

    # -- Legacy behavior (no capabilities) --

    def test_no_capabilities_network_disabled(self) -> None:
        """With enable_network=False and no capabilities, command includes --network none."""
        launcher = self._make_launcher(enable_network=False)
        cmd = launcher._build_docker_command("test:latest")
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

    def test_no_capabilities_network_enabled(self) -> None:
        """With enable_network=True and no capabilities, command does NOT include --network none."""
        launcher = self._make_launcher(enable_network=True)
        cmd = launcher._build_docker_command("test:latest")
        # Should not have --network none
        if "--network" in cmd:
            idx = cmd.index("--network")
            assert cmd[idx + 1] != "none", "Expected no --network none when enable_network=True"

    # -- Capabilities-driven behavior --

    def test_capabilities_empty_egress_denies_network(self) -> None:
        """Capabilities with empty egress -> --network none (deny all)."""
        caps = ProviderCapabilities(
            network=NetworkCapabilities(egress=()),
        )
        launcher = self._make_launcher(enable_network=True)  # enable_network should be overridden
        cmd = launcher._build_docker_command("test:latest", capabilities=caps)
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

    def test_capabilities_with_egress_allows_network(self) -> None:
        """Capabilities with egress rules -> no --network none (allow bridge)."""
        caps = ProviderCapabilities(
            network=NetworkCapabilities(
                egress=(EgressRule(host="api.openai.com", port=443, protocol="https"),),
            ),
        )
        launcher = self._make_launcher(enable_network=False)  # enable_network should be overridden
        cmd = launcher._build_docker_command("test:latest", capabilities=caps)
        # Should NOT have --network none
        network_none = False
        if "--network" in cmd:
            idx = cmd.index("--network")
            network_none = cmd[idx + 1] == "none"
        assert not network_none, "Expected no --network none when egress rules are declared"

    def test_capabilities_override_enable_network_flag(self) -> None:
        """Capabilities win over enable_network=True: empty egress -> deny."""
        caps = ProviderCapabilities(
            network=NetworkCapabilities(egress=()),
        )
        launcher = self._make_launcher(enable_network=True)
        cmd = launcher._build_docker_command("test:latest", capabilities=caps)
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none", "Capabilities should override enable_network flag"

    def test_capabilities_default_network_has_empty_egress(self) -> None:
        """Default ProviderCapabilities has NetworkCapabilities with empty egress -> deny."""
        caps = ProviderCapabilities()  # Default: network has empty egress tuple
        launcher = self._make_launcher(enable_network=True)
        cmd = launcher._build_docker_command("test:latest", capabilities=caps)
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none", "Default ProviderCapabilities has empty egress, should deny network"

    def test_capabilities_with_multiple_egress_rules(self) -> None:
        """Multiple egress rules still allow network access."""
        caps = ProviderCapabilities(
            network=NetworkCapabilities(
                egress=(
                    EgressRule(host="api.openai.com", port=443),
                    EgressRule(host="*.internal.corp", port=443),
                    EgressRule(host="redis.cache.local", port=6379, protocol="tcp"),
                ),
            ),
        )
        launcher = self._make_launcher(enable_network=False)
        cmd = launcher._build_docker_command("test:latest", capabilities=caps)
        network_none = False
        if "--network" in cmd:
            idx = cmd.index("--network")
            network_none = cmd[idx + 1] == "none"
        assert not network_none, "Multiple egress rules should allow network"

    def test_command_still_contains_security_options(self) -> None:
        """Capabilities-aware path still includes other security flags."""
        caps = ProviderCapabilities(
            network=NetworkCapabilities(egress=()),
        )
        launcher = self._make_launcher()
        cmd = launcher._build_docker_command("test:latest", capabilities=caps)
        # Core security options should still be present
        assert "--cap-drop" in cmd
        assert "--read-only" in cmd
        assert "--security-opt" in cmd
