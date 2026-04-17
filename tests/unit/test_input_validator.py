"""Focused tests for allow-list command validation."""

import pytest

from mcp_hangar.domain.security.input_validator import ALLOWED_COMMANDS, InputValidator


def test_default_allowed_commands_include_safe_mcp_runtimes():
    expected = {"uvx", "npx", "node", "python", "python3", "uv", "docker", "podman", "bun", "deno"}

    assert expected.issubset(ALLOWED_COMMANDS)


def test_validate_command_allows_known_safe_command():
    validator = InputValidator()

    result = validator.validate_command(["uvx", "mcp-server"])

    assert result.valid


def test_validate_command_blocks_unknown_command():
    validator = InputValidator()

    with pytest.raises(ValueError, match="not in the allowed command list"):
        _ = validator.validate_command(["ruby", "server.rb"])


def test_validate_command_blocks_empty_command():
    validator = InputValidator()

    result = validator.validate_command([])

    assert not result.valid


def test_allowed_commands_can_be_overridden_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_ALLOWED_COMMANDS", "custom-runner,python")

    validator = InputValidator()

    assert validator.validate_command(["custom-runner", "start"]).valid
    with pytest.raises(ValueError, match="not in the allowed command list"):
        _ = validator.validate_command(["uvx", "mcp-server"])
