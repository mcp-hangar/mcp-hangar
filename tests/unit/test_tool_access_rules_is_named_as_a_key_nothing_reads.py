"""`tool_access.rules` was never read, so a config that sets it is told (#1422).

The schema listed `rules` next to `tool_access.mode` from the day it was written
(#984), and nothing in `src` ever read it. A `rules:` block passed
`mcp-hangar config check` and `HANGAR_CONFIG_STRICT=1`, loaded without a word
and restricted nothing -- under `tool_access`, where an operator would expect it
to restrict something. It is now a key nothing reads, reported under its own
name with why: a warning by default, a refusal under `HANGAR_CONFIG_STRICT` and
from `mcp-hangar config check`.

The same check on `bootstrap(config_dict=...)` is in
`test_a_config_dict_boots_like_a_file.py`, which boots both paths for real.
"""

import re
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands.config import app as config_cli
from mcp_hangar.server.config import load_config_from_file
from mcp_hangar.server.config_schema import SECTIONS, ConfigSchemaError, validate_config

NAMED = "tool_access.rules was never read and was removed"

RULES = {"rules": [{"deny": ["delete_*"]}]}


def _config(**tool_access: Any) -> dict[str, Any]:
    # `egress` is the default topology, so loading this file sets nothing a
    # later test could inherit.
    return {
        "mcp_servers": {"m": {"mode": "subprocess", "command": ["python"]}},
        "tool_access": {"mode": "egress", **tool_access},
    }


def _write(tmp_path, config: dict[str, Any]) -> str:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config))
    return str(config_file)


def _unknown_key_warnings(logs: list[dict[str, Any]]) -> list[str]:
    return [e["detail"] for e in logs if e["log_level"] == "warning" and e["event"] == "unknown_config_key"]


def test_rules_is_not_an_allowed_key():
    # `required_catalogue` was added with a reader (#1446); `rules` never had one.
    assert SECTIONS["tool_access"] == frozenset({"mode", "required_catalogue"})
    assert "rules" not in SECTIONS["tool_access"]


def test_the_message_names_the_key_and_says_it_was_never_read():
    [problem] = validate_config(_config(**RULES))

    assert problem.startswith(NAMED)
    assert problem.endswith("Delete the key.")
    # Named, not also reported as a typo with the allowed set.
    assert "unknown key" not in problem


@pytest.mark.parametrize("value", [[], {}, None, "deny everything"])
def test_any_value_is_named(value):
    [problem] = validate_config(_config(rules=value))

    assert problem.startswith(NAMED)


def test_a_config_without_it_is_clean():
    assert validate_config(_config()) == []


def test_a_typo_beside_it_is_still_reported():
    problems = validate_config(_config(mdoe="front_door", **RULES))

    assert len(problems) == 2
    assert problems[0].startswith(NAMED)
    assert problems[1].startswith(
        "tool_access has unknown key(s) ['mdoe']; allowed keys: ['mode', 'required_catalogue']"
    )


def test_a_file_that_sets_it_loads_with_a_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGAR_CONFIG_STRICT", raising=False)

    with capture_logs() as logs:
        loaded = load_config_from_file(_write(tmp_path, _config(**RULES)))

    warnings = _unknown_key_warnings(logs)
    assert len(warnings) == 1 and warnings[0].startswith(NAMED)
    assert loaded["tool_access"]["mode"] == "egress"


def test_a_file_without_it_loads_without_a_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("HANGAR_CONFIG_STRICT", raising=False)

    with capture_logs() as logs:
        load_config_from_file(_write(tmp_path, _config()))

    assert _unknown_key_warnings(logs) == []


def test_strict_mode_refuses_a_file_that_sets_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")

    with pytest.raises(ConfigSchemaError, match=re.escape(NAMED)):
        load_config_from_file(_write(tmp_path, _config(**RULES)))


def test_strict_mode_accepts_a_file_without_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")

    loaded = load_config_from_file(_write(tmp_path, _config()))

    assert loaded["tool_access"] == {"mode": "egress"}


def test_config_check_refuses_it(tmp_path):
    result = CliRunner().invoke(config_cli, [_write(tmp_path, _config(**RULES))])

    assert result.exit_code == 1, result.output
    assert "tool_access.rules" in result.output


def test_config_check_accepts_a_config_without_it(tmp_path):
    result = CliRunner().invoke(config_cli, [_write(tmp_path, _config())])

    assert result.exit_code == 0, result.output
