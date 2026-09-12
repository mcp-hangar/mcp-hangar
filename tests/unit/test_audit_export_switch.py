"""`observability.audit.enabled` and `MCP_AUDIT_EXPORT_ENABLED` switch OTLP audit export (#1327).

Since #1318 an explicit OTLP endpoint turned audit export on, and the only way
to turn it off was to remove the endpoint, which stopped trace export too. The
switch decides whether the audit log pipeline gets the endpoint and nothing
else: tracing reads its own settings. The env var beats the file, as every
other observability setting does, and the default keeps #1318's behaviour.

The bootstrap-level proof -- no logger provider, the Null exporter, tracing
still on -- is ``tests/integration/test_audit_export_switch_bootstrap.py``.
"""

import os
from unittest.mock import patch

import pytest

from mcp_hangar.server.bootstrap import observability as obs
from mcp_hangar.server.config_schema import validate_config

ENDPOINT = "http://collector.invalid:4317"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("OTEL_", "MCP_TRACING_", "MCP_AUDIT_")):
            monkeypatch.delenv(key)


def _config(file_value: object = None) -> dict:
    """A file with an explicit endpoint and, unless None, `audit.enabled`."""
    observability: dict = {"tracing": {"otlp_endpoint": ENDPOINT}}
    if file_value is not None:
        observability["audit"] = {"enabled": file_value}
    return {"observability": observability}


@pytest.mark.parametrize(
    ("file_value", "env_value", "expected"),
    [
        (None, None, True),  # the default: #1318's behaviour
        (True, None, True),
        (False, None, False),
        (None, "true", True),
        (None, "false", False),
        (True, "true", True),
        (True, "false", False),  # the env beats the file
        (False, "true", True),  # the env beats the file
        (False, "false", False),
    ],
)
def test_the_env_var_beats_the_file_and_the_default_is_on(
    monkeypatch: pytest.MonkeyPatch, file_value: bool | None, env_value: str | None, expected: bool
) -> None:
    if env_value is not None:
        monkeypatch.setenv("MCP_AUDIT_EXPORT_ENABLED", env_value)

    parsed = obs._parse_observability_config(_config(file_value))

    assert parsed.audit_export_enabled is expected
    assert parsed.audit_otlp_endpoint == (ENDPOINT if expected else None)


@pytest.mark.parametrize("switch", ["file", "env"])
def test_off_withholds_an_endpoint_set_in_the_env(monkeypatch: pytest.MonkeyPatch, switch: str) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT)
    config: dict = {}
    if switch == "file":
        config = {"observability": {"audit": {"enabled": False}}}
    else:
        monkeypatch.setenv("MCP_AUDIT_EXPORT_ENABLED", "false")

    assert obs._parse_observability_config(config).audit_otlp_endpoint is None


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("False", False), ("0", False), ("true", True)])
def test_a_string_in_the_file_reads_as_the_env_var_does(value: str, expected: bool) -> None:
    """`enabled: ${AUDIT_EXPORT:-false}` reaches the parser as the string "false", which is truthy."""
    assert obs._parse_observability_config(_config(value)).audit_export_enabled is expected


def test_off_leaves_tracing_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    on = obs._parse_observability_config(_config())
    monkeypatch.setenv("MCP_AUDIT_EXPORT_ENABLED", "false")
    off = obs._parse_observability_config(_config())

    assert off.tracing == on.tracing
    assert off.tracing.enabled is True and off.tracing.otlp_endpoint == ENDPOINT


def test_off_gives_the_audit_pipeline_no_endpoint_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUDIT_EXPORT_ENABLED", "false")
    with (
        patch.object(obs, "init_tracing", return_value=True) as init_tracing,
        patch.object(obs, "init_audit_log_export") as init_audit,
        patch.object(obs, "logger") as logger,
    ):
        obs.init_observability(_config())

    init_audit.assert_called_once_with(None, "mcp-hangar")  # no endpoint: builds nothing, turns nothing on
    assert init_tracing.call_args.args[0].otlp_endpoint == ENDPOINT
    logger.info.assert_any_call("audit_log_export_disabled_by_config")


def test_the_file_key_is_accepted_by_the_config_schema() -> None:
    assert validate_config({"observability": {"audit": {"enabled": False}}}) == []
