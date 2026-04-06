"""Tests for cloud.config -- CloudConfig construction."""

from mcp_hangar.cloud.config import CloudConfig


class TestCloudConfig:
    def test_from_dict_disabled(self):
        assert CloudConfig.from_dict({}) is None
        assert CloudConfig.from_dict({"enabled": False}) is None

    def test_from_dict_enabled_no_key(self):
        assert CloudConfig.from_dict({"enabled": True}) is None

    def test_from_dict_enabled_with_key(self):
        cfg = CloudConfig.from_dict({
            "enabled": True,
            "license_key": "hk_v1_test",
        })
        assert cfg is not None
        assert cfg.license_key == "hk_v1_test"
        assert cfg.endpoint == "https://api.mcp-hangar.io"
        assert cfg.heartbeat_interval_s == 30

    def test_from_dict_custom_values(self):
        cfg = CloudConfig.from_dict({
            "enabled": True,
            "license_key": "hk_v1_test",
            "endpoint": "https://custom.example.com",
            "batch_interval_s": 5,
            "heartbeat_interval_s": 15,
            "state_sync_interval_s": 120,
            "buffer_max_size": 5000,
        })
        assert cfg is not None
        assert cfg.endpoint == "https://custom.example.com"
        assert cfg.batch_interval_s == 5
        assert cfg.heartbeat_interval_s == 15
        assert cfg.state_sync_interval_s == 120
        assert cfg.buffer_max_size == 5000

    def test_frozen(self):
        cfg = CloudConfig(license_key="test")
        try:
            cfg.license_key = "other"  # type: ignore[misc]
            assert False, "Should raise"
        except AttributeError:
            pass
