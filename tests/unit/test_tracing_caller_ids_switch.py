"""`observability.tracing.caller_ids` and `MCP_TRACING_CALLER_IDS` put caller ids on spans (#1580).

The #1276 contract keeps the caller's user, agent and session ids off spans
unless the operator opts in. The switch is read once, at bootstrap, into the
tracing module: the executor asks that module per call and never reads the
environment. The env var beats the file, as every other observability setting
does. What a span then carries is
``tests/integration/test_caller_ids_on_the_served_app.py``.
"""

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from mcp_hangar.observability import tracing
from mcp_hangar.server.bootstrap import observability as obs
from mcp_hangar.server.config_schema import validate_config


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith(("OTEL_", "MCP_TRACING_", "MCP_AUDIT_")):
            monkeypatch.delenv(key)
    yield
    tracing.set_caller_ids_on_spans(False)


def _config(file_value: object = None) -> dict:
    tracing_section: dict = {}
    if file_value is not None:
        tracing_section["caller_ids"] = file_value
    return {"observability": {"tracing": tracing_section}}


@pytest.mark.parametrize(
    ("file_value", "env_value", "expected"),
    [
        (None, None, False),  # the default: the contract's
        (True, None, True),
        (False, None, False),
        (None, "true", True),
        (None, "false", False),
        (True, "false", False),  # the env beats the file
        (False, "true", True),  # the env beats the file
        ("false", None, False),  # `${VAR:-false}` reaches the parser as a string
        ("true", None, True),
    ],
)
def test_the_env_var_beats_the_file_and_the_default_is_off(
    monkeypatch: pytest.MonkeyPatch, file_value: object, env_value: str | None, expected: bool
) -> None:
    if env_value is not None:
        monkeypatch.setenv("MCP_TRACING_CALLER_IDS", env_value)

    assert obs._parse_observability_config(_config(file_value)).tracing.caller_ids is expected


@pytest.mark.parametrize("opted_in", [True, False])
def test_bootstrap_hands_the_switch_to_the_tracing_module(opted_in: bool) -> None:
    tracing.set_caller_ids_on_spans(not opted_in)  # whatever an earlier bootstrap left
    with (
        patch.object(obs, "init_tracing", return_value=True),
        patch.object(obs, "init_audit_log_export"),
        patch.object(obs, "logger") as logger,
    ):
        obs.init_observability(_config(opted_in))

    assert tracing.caller_ids_on_spans() is opted_in
    assert (("tracing_caller_ids_enabled_by_config",) in [c.args for c in logger.info.call_args_list]) is opted_in


def test_the_file_key_is_accepted_by_the_config_schema() -> None:
    assert validate_config(_config(True)) == []
