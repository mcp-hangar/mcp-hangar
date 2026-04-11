"""Cloud connector configuration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CloudConfig:
    """Configuration for the cloud connector.

    All fields have sensible defaults. Only ``license_key`` is required
    for the connector to activate.
    """

    license_key: str
    endpoint: str = "https://api.mcp-hangar.io"
    batch_interval_s: int = 10
    heartbeat_interval_s: int = 30
    state_sync_interval_s: int = 60
    buffer_max_size: int = 10_000
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 30.0
    max_registration_attempts: int = 10
    dormant_probe_interval_s: int = 300

    @staticmethod
    def from_dict(d: dict) -> "CloudConfig | None":
        """Build config from a ``cloud:`` YAML section.

        Returns ``None`` when the section is absent or disabled.
        """
        if not d:
            return None
        if not d.get("enabled", False):
            return None
        key = d.get("license_key", "")
        if not key:
            return None
        return CloudConfig(
            license_key=key,
            endpoint=d.get("endpoint", CloudConfig.endpoint),
            batch_interval_s=int(d.get("batch_interval_s", CloudConfig.batch_interval_s)),
            heartbeat_interval_s=int(d.get("heartbeat_interval_s", CloudConfig.heartbeat_interval_s)),
            state_sync_interval_s=int(d.get("state_sync_interval_s", CloudConfig.state_sync_interval_s)),
            buffer_max_size=int(d.get("buffer_max_size", CloudConfig.buffer_max_size)),
            max_registration_attempts=int(d.get("max_registration_attempts", CloudConfig.max_registration_attempts)),
            dormant_probe_interval_s=int(d.get("dormant_probe_interval_s", CloudConfig.dormant_probe_interval_s)),
        )
