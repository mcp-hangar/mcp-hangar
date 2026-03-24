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


class TestProviderCapabilitiesFromDict:
    """Tests for ProviderCapabilities.from_dict() YAML deserialization factory."""

    def test_from_dict_none_returns_default(self) -> None:
        cap = ProviderCapabilities.from_dict(None)
        assert cap == ProviderCapabilities()
        assert cap.enforcement_mode == "alert"

    def test_from_dict_empty_dict_returns_default(self) -> None:
        cap = ProviderCapabilities.from_dict({})
        assert cap == ProviderCapabilities()

    def test_from_dict_full_config(self) -> None:
        config = {
            "network": {
                "egress": [
                    {"host": "api.openai.com", "port": 443, "protocol": "https"},
                    {"host": "*.internal.corp", "port": 8080, "protocol": "http"},
                ],
                "dns_allowed": False,
                "loopback_allowed": True,
            },
            "filesystem": {
                "read_paths": ["/data/kb"],
                "write_paths": ["/tmp/cache"],
                "temp_allowed": False,
            },
            "environment": {
                "required": ["OPENAI_API_KEY"],
                "optional": ["LOG_LEVEL"],
            },
            "tools": {
                "max_count": 50,
                "schema_drift_alert": False,
            },
            "resources": {
                "max_memory_mb": 512,
                "max_cpu_percent": 75.0,
            },
            "enforcement_mode": "block",
        }
        cap = ProviderCapabilities.from_dict(config)

        # Network
        assert len(cap.network.egress) == 2
        assert cap.network.egress[0].host == "api.openai.com"
        assert cap.network.egress[0].port == 443
        assert cap.network.egress[1].host == "*.internal.corp"
        assert cap.network.egress[1].port == 8080
        assert cap.network.egress[1].protocol == "http"
        assert cap.network.dns_allowed is False
        assert cap.network.loopback_allowed is True

        # Filesystem
        assert cap.filesystem.read_paths == ("/data/kb",)
        assert cap.filesystem.write_paths == ("/tmp/cache",)
        assert cap.filesystem.temp_allowed is False

        # Environment
        assert cap.environment.required == ("OPENAI_API_KEY",)
        assert cap.environment.optional == ("LOG_LEVEL",)

        # Tools
        assert cap.tools.max_count == 50
        assert cap.tools.schema_drift_alert is False

        # Resources
        assert cap.resources.max_memory_mb == 512
        assert cap.resources.max_cpu_percent == 75.0

        # Enforcement
        assert cap.enforcement_mode == "block"

    def test_from_dict_partial_config_uses_defaults(self) -> None:
        config = {
            "network": {
                "egress": [{"host": "example.com"}],
            },
        }
        cap = ProviderCapabilities.from_dict(config)
        assert len(cap.network.egress) == 1
        assert cap.network.egress[0].port == 443  # default
        assert cap.network.egress[0].protocol == "https"  # default
        assert cap.filesystem == FilesystemCapabilities()  # default
        assert cap.enforcement_mode == "alert"  # default

    def test_from_dict_invalid_enforcement_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="enforcement_mode must be one of"):
            ProviderCapabilities.from_dict({"enforcement_mode": "ignore"})

    def test_from_dict_invalid_egress_empty_host_raises(self) -> None:
        with pytest.raises(ValueError, match="host cannot be empty"):
            ProviderCapabilities.from_dict(
                {
                    "network": {"egress": [{"host": "", "port": 443}]},
                }
            )

    def test_from_dict_invalid_egress_bad_port_raises(self) -> None:
        with pytest.raises(ValueError, match="port must be 0-65535"):
            ProviderCapabilities.from_dict(
                {
                    "network": {"egress": [{"host": "example.com", "port": 99999}]},
                }
            )


class TestProviderCapabilitiesOnProvider:
    """Tests for capabilities parameter on Provider aggregate."""

    def test_provider_with_capabilities(self) -> None:
        from mcp_hangar.domain.model.provider import Provider

        cap = ProviderCapabilities.default()
        provider = Provider(provider_id="test", mode="subprocess", capabilities=cap)
        assert provider.capabilities is cap

    def test_provider_without_capabilities_defaults_to_none(self) -> None:
        from mcp_hangar.domain.model.provider import Provider

        provider = Provider(provider_id="test", mode="subprocess")
        assert provider.capabilities is None

    def test_provider_capabilities_property_returns_stored_value(self) -> None:
        from mcp_hangar.domain.model.provider import Provider

        cap = ProviderCapabilities(enforcement_mode="quarantine")
        provider = Provider(provider_id="test", mode="subprocess", capabilities=cap)
        assert provider.capabilities is not None
        assert provider.capabilities.enforcement_mode == "quarantine"
