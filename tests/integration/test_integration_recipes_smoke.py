"""Smoke tests for all examples/ integration recipes.

Validates YAML syntax and required structure. No Docker required.
Runs in CI on every PR to catch broken example configs.
"""

import pathlib

import pytest
import yaml


class TestOpenLITRecipe:
    """examples/openlit/ must have valid config files."""

    def test_docker_compose_is_valid_yaml(self) -> None:
        path = pathlib.Path("examples/openlit/docker-compose.yml")
        assert path.exists(), f"Missing {path}"
        config = yaml.safe_load(path.read_text())
        assert config is not None

    def test_docker_compose_has_openlit_service(self) -> None:
        config = yaml.safe_load(pathlib.Path("examples/openlit/docker-compose.yml").read_text())
        services = config.get("services", {})
        assert "openlit" in services, "OpenLIT recipe must include openlit service"
        assert "mcp-hangar" in services, "OpenLIT recipe must include mcp-hangar service"
        assert "otel-collector" in services, "OpenLIT recipe must include otel-collector service"

    def test_collector_config_is_valid_yaml(self) -> None:
        path = pathlib.Path("examples/openlit/otel-collector-config.yaml")
        assert path.exists(), f"Missing {path}"
        config = yaml.safe_load(path.read_text())
        assert config is not None

    def test_collector_config_forwards_to_openlit(self) -> None:
        config = yaml.safe_load(pathlib.Path("examples/openlit/otel-collector-config.yaml").read_text())
        exporters = config.get("exporters", {})
        # Must have an OTLP exporter (forwards to OpenLIT)
        otlp_exporters = [k for k in exporters if k.startswith("otlp")]
        assert len(otlp_exporters) >= 1, "Collector must forward to OpenLIT via OTLP exporter"


class TestLangfuseRecipe:
    """examples/langfuse/ must have a README."""

    def test_readme_exists(self) -> None:
        path = pathlib.Path("examples/langfuse/README.md")
        assert path.exists(), f"Missing {path}"
        content = path.read_text()
        assert "LANGFUSE_SECRET_KEY" in content, "Langfuse README must document secret key config"
        assert "LANGFUSE_PUBLIC_KEY" in content, "Langfuse README must document public key config"

    def test_readme_warns_about_secrets(self) -> None:
        content = pathlib.Path("examples/langfuse/README.md").read_text()
        assert "secret" in content.lower(), "Langfuse README must warn about secret handling"
