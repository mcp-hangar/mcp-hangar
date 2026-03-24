"""Unit tests for ProviderCapabilities value object.

Tests for the capability declaration schema introduced in
PRODUCT_ARCHITECTURE.md Phase 1 (P0).
"""

import pytest

from mcp_hangar.domain.value_objects.capabilities import (
    EgressRule,
    EnvironmentCapabilities,
    FilesystemCapabilities,
    NetworkCapabilities,
    ProviderCapabilities,
    ResourceCapabilities,
    ToolCapabilities,
)


class TestEgressRule:
    def test_valid_egress_rule(self) -> None:
        rule = EgressRule(host="api.openai.com", port=443, protocol="https")
        assert rule.host == "api.openai.com"
        assert rule.port == 443
        assert rule.protocol == "https"

    def test_glob_host_is_valid(self) -> None:
        rule = EgressRule(host="*.internal.corp", port=443)
        assert rule.host == "*.internal.corp"

    def test_empty_host_raises(self) -> None:
        with pytest.raises(ValueError, match="host cannot be empty"):
            EgressRule(host="", port=443)

    def test_invalid_port_raises(self) -> None:
        with pytest.raises(ValueError, match="port must be 0-65535"):
            EgressRule(host="example.com", port=99999)

    def test_invalid_protocol_raises(self) -> None:
        with pytest.raises(ValueError, match="protocol must be one of"):
            EgressRule(host="example.com", port=443, protocol="ftp")

    def test_any_port_zero(self) -> None:
        rule = EgressRule(host="example.com", port=0, protocol="any")
        assert rule.port == 0


class TestNetworkCapabilities:
    def test_default_is_empty_egress(self) -> None:
        net = NetworkCapabilities()
        assert net.egress == ()
        assert net.dns_allowed is True

    def test_deny_all_preset(self) -> None:
        net = NetworkCapabilities.deny_all()
        assert net.egress == ()
        assert net.dns_allowed is False
        assert net.loopback_allowed is False

    def test_allow_all_preset(self) -> None:
        net = NetworkCapabilities.allow_all()
        assert len(net.egress) == 1
        assert net.egress[0].host == "*"

    def test_egress_coerced_to_tuple(self) -> None:
        rule = EgressRule(host="api.openai.com", port=443)
        net = NetworkCapabilities(egress=[rule])  # type: ignore[arg-type]
        assert isinstance(net.egress, tuple)


class TestFilesystemCapabilities:
    def test_read_only_preset(self) -> None:
        fs = FilesystemCapabilities.read_only("/data", "/config")
        assert "/data" in fs.read_paths
        assert fs.write_paths == ()
        assert fs.temp_allowed is False

    def test_none_preset(self) -> None:
        fs = FilesystemCapabilities.none()
        assert fs.read_paths == ()
        assert fs.write_paths == ()
        assert fs.temp_allowed is False


class TestEnvironmentCapabilities:
    def test_all_declared(self) -> None:
        env = EnvironmentCapabilities(
            required=("OPENAI_API_KEY",),
            optional=("LOG_LEVEL", "DEBUG"),
        )
        assert env.all_declared() == {"OPENAI_API_KEY", "LOG_LEVEL", "DEBUG"}

    def test_empty_by_default(self) -> None:
        env = EnvironmentCapabilities()
        assert env.all_declared() == frozenset()


class TestToolCapabilities:
    def test_defaults(self) -> None:
        tools = ToolCapabilities()
        assert tools.max_count == 0
        assert tools.schema_drift_alert is True

    def test_negative_max_count_raises(self) -> None:
        with pytest.raises(ValueError, match="max_count cannot be negative"):
            ToolCapabilities(max_count=-1)


class TestResourceCapabilities:
    def test_defaults_are_unlimited(self) -> None:
        res = ResourceCapabilities()
        assert res.max_memory_mb == 0
        assert res.max_cpu_percent == 0.0

    def test_negative_memory_raises(self) -> None:
        with pytest.raises(ValueError, match="max_memory_mb cannot be negative"):
            ResourceCapabilities(max_memory_mb=-1)

    def test_negative_cpu_raises(self) -> None:
        with pytest.raises(ValueError, match="max_cpu_percent cannot be negative"):
            ResourceCapabilities(max_cpu_percent=-0.1)


class TestProviderCapabilities:
    def test_default_preset(self) -> None:
        cap = ProviderCapabilities.default()
        assert cap.enforcement_mode == "alert"

    def test_strict_preset(self) -> None:
        cap = ProviderCapabilities.strict()
        assert cap.enforcement_mode == "block"
        assert cap.network.dns_allowed is False
        assert cap.filesystem.write_paths == ()

    def test_invalid_enforcement_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="enforcement_mode must be one of"):
            ProviderCapabilities(enforcement_mode="ignore")

    def test_has_egress_rules_false_when_empty(self) -> None:
        cap = ProviderCapabilities.default()
        assert cap.has_egress_rules() is False

    def test_has_egress_rules_true_when_declared(self) -> None:
        rule = EgressRule(host="api.openai.com", port=443)
        cap = ProviderCapabilities(
            network=NetworkCapabilities(egress=(rule,)),
        )
        assert cap.has_egress_rules() is True

    def test_immutable(self) -> None:
        cap = ProviderCapabilities.default()
        with pytest.raises((AttributeError, TypeError)):
            cap.enforcement_mode = "block"  # type: ignore[misc]
