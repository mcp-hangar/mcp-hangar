"""Smoke tests for examples/otel-collector/ config files.

Validates YAML syntax and required keys without starting Docker.
Runs in CI on every PR.
"""

import pathlib

import pytest
import yaml


EXAMPLES_DIR = pathlib.Path("examples/otel-collector")


class TestOtelCollectorConfig:
    """Config files must be valid YAML with required structure."""

    def test_docker_compose_is_valid_yaml(self) -> None:
        compose_path = EXAMPLES_DIR / "docker-compose.yml"
        assert compose_path.exists(), f"Missing {compose_path}"
        config = yaml.safe_load(compose_path.read_text())
        assert config is not None

    def test_docker_compose_has_required_services(self) -> None:
        config = yaml.safe_load((EXAMPLES_DIR / "docker-compose.yml").read_text())
        services = config.get("services", {})
        assert "mcp-hangar" in services, "docker-compose must include mcp-hangar service"
        assert "otel-collector" in services, "docker-compose must include otel-collector service"
        assert "prometheus" in services, "docker-compose must include prometheus service"

    def test_docker_compose_hangar_has_otlp_endpoint(self) -> None:
        config = yaml.safe_load((EXAMPLES_DIR / "docker-compose.yml").read_text())
        hangar_env = config["services"]["mcp-hangar"]["environment"]
        env_dict = hangar_env if isinstance(hangar_env, dict) else dict(e.split("=", 1) for e in hangar_env if "=" in e)
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in env_dict, "mcp-hangar service must set OTEL_EXPORTER_OTLP_ENDPOINT"

    def test_collector_config_is_valid_yaml(self) -> None:
        collector_path = EXAMPLES_DIR / "otel-collector-config.yaml"
        assert collector_path.exists(), f"Missing {collector_path}"
        config = yaml.safe_load(collector_path.read_text())
        assert config is not None

    def test_collector_config_has_otlp_receiver(self) -> None:
        config = yaml.safe_load((EXAMPLES_DIR / "otel-collector-config.yaml").read_text())
        assert "otlp" in config.get("receivers", {}), "Collector must have OTLP receiver"

    def test_collector_config_has_traces_metrics_logs_pipelines(self) -> None:
        config = yaml.safe_load((EXAMPLES_DIR / "otel-collector-config.yaml").read_text())
        pipelines = config.get("service", {}).get("pipelines", {})
        assert "traces" in pipelines
        assert "metrics" in pipelines
        assert "logs" in pipelines
