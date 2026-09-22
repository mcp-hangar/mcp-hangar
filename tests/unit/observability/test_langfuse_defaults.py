"""Guards the secure-by-default posture of Langfuse content export.

Two defaults for one flag disagreed (#1534): ``LangfuseConfig`` scrubbed, while
the environment path in ``bootstrap/runtime.py`` defaulted to not scrubbing and
passed that through, so enabling Langfuse with nothing but credentials shipped
raw tool inputs and outputs. Every place that carries a scrub flag now reads
``SCRUB_PAYLOADS_BY_DEFAULT``; these tests fail if any of them states its own
default again, and if the environment variables stop being an opt-out.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from mcp_hangar.application.ports.observability import SCRUB_PAYLOADS_BY_DEFAULT, NullObservabilityAdapter
from mcp_hangar.bootstrap.runtime import ObservabilityConfig, create_runtime
from mcp_hangar.domain.security.rate_limiter import reset_rate_limiter
from mcp_hangar.integrations import langfuse as langfuse_module
from mcp_hangar.integrations.langfuse import LangfuseConfig
from mcp_hangar.server.bootstrap.observability import LangfuseBootstrapConfig, _parse_observability_config

CREDENTIALS = {
    "HANGAR_LANGFUSE_ENABLED": "true",
    "LANGFUSE_PUBLIC_KEY": "pk-placeholder",
    "LANGFUSE_SECRET_KEY": "sk-placeholder",
}


def test_the_stated_default_is_to_scrub() -> None:
    assert SCRUB_PAYLOADS_BY_DEFAULT is True


def test_langfuse_scrubbing_is_on_by_default() -> None:
    cfg = LangfuseConfig()
    assert cfg.scrub_inputs is True
    assert cfg.scrub_outputs is True


def test_bootstrap_langfuse_scrubbing_is_on_by_default() -> None:
    cfg = LangfuseBootstrapConfig()
    assert cfg.scrub_inputs is True
    assert cfg.scrub_outputs is True


def test_runtime_observability_config_scrubs_by_default() -> None:
    cfg = ObservabilityConfig()
    assert cfg.langfuse_scrub_inputs is True
    assert cfg.langfuse_scrub_outputs is True


def test_config_file_without_scrub_keys_scrubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_LANGFUSE_SCRUB_INPUTS", raising=False)
    monkeypatch.delenv("MCP_LANGFUSE_SCRUB_OUTPUTS", raising=False)
    parsed = _parse_observability_config({"observability": {"langfuse": {"enabled": True}}})
    assert parsed.langfuse.scrub_inputs is True
    assert parsed.langfuse.scrub_outputs is True


@pytest.fixture
def built_configs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Record the ``LangfuseConfig`` ``create_runtime`` builds, without a real client."""
    seen: list[Any] = []

    class _Recorder(NullObservabilityAdapter):
        def __init__(self, config: Any) -> None:
            seen.append(config)

    monkeypatch.setattr(langfuse_module, "LangfuseObservabilityAdapter", _Recorder)
    reset_rate_limiter()
    yield seen
    reset_rate_limiter()


def test_enabling_langfuse_with_only_credentials_scrubs(built_configs: list[Any]) -> None:
    create_runtime(env=dict(CREDENTIALS))

    (config,) = built_configs
    assert config.scrub_inputs is True
    assert config.scrub_outputs is True


@pytest.mark.parametrize("value", ["false", "FALSE", " 0 ", "no", "off"])
def test_the_env_vars_are_an_opt_out(built_configs: list[Any], value: str) -> None:
    create_runtime(
        env={**CREDENTIALS, "HANGAR_LANGFUSE_SCRUB_INPUTS": value, "HANGAR_LANGFUSE_SCRUB_OUTPUTS": value},
    )

    (config,) = built_configs
    assert config.scrub_inputs is False
    assert config.scrub_outputs is False


def test_each_env_var_opts_out_on_its_own(built_configs: list[Any]) -> None:
    create_runtime(env={**CREDENTIALS, "HANGAR_LANGFUSE_SCRUB_OUTPUTS": "false"})

    (config,) = built_configs
    assert config.scrub_inputs is True
    assert config.scrub_outputs is False


@pytest.mark.parametrize("value", ["true", "", "flase", "maybe"])
def test_anything_but_an_explicit_false_keeps_scrubbing(built_configs: list[Any], value: str) -> None:
    create_runtime(
        env={**CREDENTIALS, "HANGAR_LANGFUSE_SCRUB_INPUTS": value, "HANGAR_LANGFUSE_SCRUB_OUTPUTS": value},
    )

    (config,) = built_configs
    assert config.scrub_inputs is True
    assert config.scrub_outputs is True
