"""Tests for server/cli.py module.

Tests cover CLI argument parsing and environment variable resolution.
These tests have no side effects - they only test pure parsing logic.
"""

import os
from unittest.mock import patch

import pytest

from mcp_hangar.server.cli import CLIConfig, parse_args


class TestCLIConfig:
    """Tests for CLIConfig dataclass."""

    def test_cli_config_is_frozen(self):
        """CLIConfig should be immutable (frozen)."""
        config = CLIConfig(
            http_mode=False,
            http_host="0.0.0.0",
            http_port=8000,
            config_path=None,
            log_file=None,
            log_level="INFO",
            json_logs=False,
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            config.http_mode = True

    def test_cli_config_equality(self):
        """CLIConfig instances with same values should be equal."""
        config1 = CLIConfig(
            http_mode=True,
            http_host="localhost",
            http_port=9000,
            config_path="config.yaml",
            log_file="server.log",
            log_level="DEBUG",
            json_logs=True,
        )
        config2 = CLIConfig(
            http_mode=True,
            http_host="localhost",
            http_port=9000,
            config_path="config.yaml",
            log_file="server.log",
            log_level="DEBUG",
            json_logs=True,
        )

        assert config1 == config2


class TestParseArgsDefaults:
    """Tests for parse_args with default values."""

    def test_parse_args_defaults(self):
        """Default values without any arguments."""
        # Clear any env vars that might interfere
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args([])

        assert config.http_mode is False
        assert config.http_host == "0.0.0.0"
        assert config.http_port == 8000
        assert config.config_path is None
        assert config.log_file is None
        assert config.log_level == "INFO"
        assert config.json_logs is False

    def test_parse_args_returns_cli_config(self):
        """parse_args should return a CLIConfig instance."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args([])

        assert isinstance(config, CLIConfig)


class TestParseArgsHTTPMode:
    """Tests for HTTP mode flag parsing."""

    def test_parse_args_http_mode_flag(self):
        """--http flag enables HTTP mode."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--http"])

        assert config.http_mode is True

    def test_parse_args_http_mode_env(self):
        """MCP_MODE=http enables HTTP mode."""
        with patch.dict(os.environ, {"MCP_MODE": "http"}, clear=True):
            config = parse_args([])

        assert config.http_mode is True

    def test_parse_args_http_flag_overrides_env(self):
        """CLI --http flag works even if env is stdio."""
        with patch.dict(os.environ, {"MCP_MODE": "stdio"}, clear=True):
            config = parse_args(["--http"])

        assert config.http_mode is True


class TestParseArgsHostPort:
    """Tests for host and port parsing."""

    def test_parse_args_host_option(self):
        """--host sets HTTP host."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--host", "127.0.0.1"])

        assert config.http_host == "127.0.0.1"

    def test_parse_args_port_option(self):
        """--port sets HTTP port."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--port", "9000"])

        assert config.http_port == 9000

    def test_parse_args_port_override(self):
        """--port overrides default and env."""
        with patch.dict(os.environ, {"MCP_HTTP_PORT": "7000"}, clear=True):
            config = parse_args(["--port", "9000"])

        assert config.http_port == 9000

    def test_parse_args_host_from_env(self):
        """MCP_HTTP_HOST sets host."""
        with patch.dict(os.environ, {"MCP_HTTP_HOST": "192.168.1.1"}, clear=True):
            config = parse_args([])

        assert config.http_host == "192.168.1.1"

    def test_parse_args_port_from_env(self):
        """MCP_HTTP_PORT sets port."""
        with patch.dict(os.environ, {"MCP_HTTP_PORT": "7777"}, clear=True):
            config = parse_args([])

        assert config.http_port == 7777


class TestParseArgsConfig:
    """Tests for config path parsing."""

    def test_parse_args_config_path(self):
        """--config sets config path."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--config", "/path/to/config.yaml"])

        assert config.config_path == "/path/to/config.yaml"

    def test_parse_args_config_path_relative(self):
        """--config accepts relative paths."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--config", "config.yaml"])

        assert config.config_path == "config.yaml"


class TestParseArgsLogging:
    """Tests for logging options parsing."""

    def test_parse_args_log_file(self):
        """--log-file sets log file path."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--log-file", "/var/log/mcp.log"])

        assert config.log_file == "/var/log/mcp.log"

    def test_parse_args_log_level(self):
        """--log-level sets log level."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--log-level", "DEBUG"])

        assert config.log_level == "DEBUG"

    def test_parse_args_log_level_from_env(self):
        """MCP_LOG_LEVEL sets log level."""
        with patch.dict(os.environ, {"MCP_LOG_LEVEL": "WARNING"}, clear=True):
            config = parse_args([])

        assert config.log_level == "WARNING"

    def test_parse_args_json_logs_flag(self):
        """--json-logs enables JSON formatted logs."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(["--json-logs"])

        assert config.json_logs is True

    def test_parse_args_json_logs_from_env(self):
        """MCP_JSON_LOGS=true enables JSON formatted logs."""
        with patch.dict(os.environ, {"MCP_JSON_LOGS": "true"}, clear=True):
            config = parse_args([])

        assert config.json_logs is True

    def test_parse_args_json_logs_env_false(self):
        """MCP_JSON_LOGS=false keeps JSON logs disabled."""
        with patch.dict(os.environ, {"MCP_JSON_LOGS": "false"}, clear=True):
            config = parse_args([])

        assert config.json_logs is False


class TestParseArgsCombined:
    """Tests for combined argument parsing."""

    def test_parse_args_all_options_combined(self):
        """Should parse all options together."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args(
                [
                    "--http",
                    "--host",
                    "localhost",
                    "--port",
                    "8080",
                    "--config",
                    "custom.yaml",
                    "--log-file",
                    "server.log",
                    "--log-level",
                    "DEBUG",
                    "--json-logs",
                ]
            )

        assert config.http_mode is True
        assert config.http_host == "localhost"
        assert config.http_port == 8080
        assert config.config_path == "custom.yaml"
        assert config.log_file == "server.log"
        assert config.log_level == "DEBUG"
        assert config.json_logs is True

    def test_parse_args_cli_overrides_all_env(self):
        """CLI arguments should override all environment variables."""
        env_vars = {
            "MCP_MODE": "stdio",
            "MCP_HTTP_HOST": "env-host",
            "MCP_HTTP_PORT": "7000",
            "MCP_LOG_LEVEL": "ERROR",
            "MCP_JSON_LOGS": "true",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = parse_args(
                [
                    "--http",
                    "--host",
                    "cli-host",
                    "--port",
                    "9000",
                    "--log-level",
                    "DEBUG",
                ]
            )

        # CLI overrides
        assert config.http_mode is True  # --http overrides MCP_MODE=stdio
        assert config.http_host == "cli-host"  # --host overrides MCP_HTTP_HOST
        assert config.http_port == 9000  # --port overrides MCP_HTTP_PORT
        assert config.log_level == "DEBUG"  # --log-level overrides MCP_LOG_LEVEL
        # ENV still applies when CLI doesn't specify
        assert config.json_logs is True  # MCP_JSON_LOGS applies (no CLI override)


class TestParseArgsEdgeCases:
    """Tests for edge cases in argument parsing."""

    def test_parse_args_empty_list(self):
        """Empty args list should use defaults."""
        with patch.dict(os.environ, {}, clear=True):
            config = parse_args([])

        assert config.http_mode is False
        assert config.http_port == 8000

    def test_parse_args_none_uses_sys_argv(self):
        """None args should use sys.argv (tested via mock)."""
        import sys

        original_argv = sys.argv.copy()
        try:
            sys.argv = ["mcp-hangar", "--http", "--port", "5000"]
            with patch.dict(os.environ, {}, clear=True):
                config = parse_args(None)

            assert config.http_mode is True
            assert config.http_port == 5000
        finally:
            sys.argv = original_argv

    def test_parse_args_log_level_case_insensitive_env(self):
        """MCP_LOG_LEVEL should be case-insensitive."""
        with patch.dict(os.environ, {"MCP_LOG_LEVEL": "debug"}, clear=True):
            config = parse_args([])

        assert config.log_level == "DEBUG"
