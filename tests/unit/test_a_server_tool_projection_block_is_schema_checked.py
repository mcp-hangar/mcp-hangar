"""A server's own `tool_projection` block is schema-checked, and `config check` checks values (#1733).

`pin_recheck_interval_s` written under a server's `tool_projection` (or any
typo) was ignored without a word, and `config check` passed
`tool_projection.pin_recheck_interval_s: 2`, which then refused the boot.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands.config import app
from mcp_hangar.server.config import _WITHDRAWAL_KEYS
from mcp_hangar.server.config_schema import (
    TOOL_PROJECTION_SCOPE_KEYS,
    TOOL_PROJECTION_TENANT_KEYS,
    validate_config,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "mcp_hangar" / "server" / "config.py"


def _keys_read_with_get(function: str, variable: str) -> set[str]:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == function:
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == variable
                    and isinstance(node.args[0], ast.Constant)
                ):
                    found.add(node.args[0].value)
    return found


def test_the_schema_copies_hold_to_what_the_reader_looks_up():
    scope = _keys_read_with_get("_register_tool_projection_block", "tool_projection_config")
    assert scope == {"digest_enforcement", "pins", "tenant_overrides", "upstream"}, (
        f"the scan or the reader changed: {scope}"
    )
    assert scope | set(_WITHDRAWAL_KEYS) == TOOL_PROJECTION_SCOPE_KEYS
    assert {"pins"} | set(_WITHDRAWAL_KEYS) == TOOL_PROJECTION_TENANT_KEYS


def _server(**block):
    return {"mcp_servers": {"s": {"mode": "subprocess", "command": ["x"], "tool_projection": block}}}


def test_a_gateway_wide_key_under_a_server_is_reported_with_where_it_belongs():
    problems = validate_config(_server(pin_recheck_interval_s=30))
    assert len(problems) == 1
    assert "mcp_servers.s.tool_projection" in problems[0]
    assert "pin_recheck_interval_s" in problems[0]
    assert "top-level" in problems[0]


def test_a_typo_in_a_server_block_and_in_a_tenant_override_is_reported():
    problems = validate_config(_server(withdrawn=["a"], widthdrawn=["b"], tenant_overrides={"t": {"pinz": {}}}))
    assert any("widthdrawn" in p for p in problems)
    assert any("tenant_overrides.t" in p and "pinz" in p for p in problems)


def test_every_key_the_reader_reads_passes():
    block = {
        "digest_enforcement": "block",
        "pins": {"a": "x"},
        "withdrawn": ["a"],
        "withdrawn_prompts": ["p"],
        "withdrawn_resources": ["r"],
        "tenant_overrides": {"t": {"pins": {}, "withdrawn": ["a"]}},
    }
    assert validate_config(_server(**block)) == []


def test_a_group_block_is_checked_too():
    config = {"mcp_servers": {"g": {"mode": "group", "members": [], "tool_projection": {"oops": 1}}}}
    assert any("mcp_servers.g.tool_projection" in p for p in validate_config(config))


def _check(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return CliRunner().invoke(app, [str(path)])


def test_config_check_fails_on_a_pin_recheck_interval_the_boot_refuses(tmp_path):
    result = _check(tmp_path, {"tool_projection": {"pin_recheck_interval_s": 2}})
    assert result.exit_code == 1
    assert "pin_recheck_interval_s" in result.output


def test_config_check_fails_on_a_failure_threshold_the_boot_refuses(tmp_path):
    config = {"mcp_servers": {"s": {"mode": "subprocess", "command": ["x"], "max_consecutive_failures": 500}}}
    result = _check(tmp_path, config)
    assert result.exit_code == 1
    assert "max_consecutive_failures" in result.output


def test_config_check_passes_values_the_boot_accepts(tmp_path):
    config = {
        "tool_projection": {"pin_recheck_interval_s": 60},
        "mcp_servers": {"s": {"mode": "subprocess", "command": ["x"], "max_consecutive_failures": 5}},
    }
    result = _check(tmp_path, config)
    assert result.exit_code == 0, result.output
