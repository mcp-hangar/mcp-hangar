"""Tests for CLI errors module.

Tests cover error formatting and error types.
"""

from mcp_hangar.server.cli.errors import (
    ClaudeDesktopNotFoundError,
    CLIError,
    ConfigNotFoundError,
    PermissionError,
    ProviderNotFoundError,
    ProviderStartError,
)


class TestCLIError:
    """Tests for base CLIError class."""

    def test_cli_error_message(self):
        """CLIError should have message."""
        error = CLIError(message="Something went wrong")
        assert str(error) == "Something went wrong"

    def test_cli_error_with_reason(self):
        """CLIError should support reason."""
        error = CLIError(
            message="Failed",
            reason="The server is down",
        )
        assert error.reason == "The server is down"

    def test_cli_error_with_suggestions(self):
        """CLIError should support suggestions."""
        error = CLIError(
            message="Failed",
            suggestions=["Try again", "Check logs"],
        )
        assert len(error.suggestions) == 2
        assert "Try again" in error.suggestions

    def test_cli_error_default_exit_code(self):
        """CLIError should default to exit code 1."""
        error = CLIError(message="Error")
        assert error.exit_code == 1

    def test_cli_error_custom_exit_code(self):
        """CLIError should support custom exit code."""
        error = CLIError(message="Error", exit_code=2)
        assert error.exit_code == 2


class TestConfigNotFoundError:
    """Tests for ConfigNotFoundError."""

    def test_includes_path_in_message(self):
        """Error message should include the path."""
        error = ConfigNotFoundError("/path/to/config.yaml")
        assert "/path/to/config.yaml" in str(error)

    def test_has_helpful_suggestions(self):
        """Should have suggestions for fixing."""
        error = ConfigNotFoundError("/path/to/config.yaml")
        assert len(error.suggestions) > 0
        assert any("init" in s for s in error.suggestions)


class TestClaudeDesktopNotFoundError:
    """Tests for ClaudeDesktopNotFoundError."""

    def test_includes_searched_paths(self):
        """Error should include searched paths."""
        error = ClaudeDesktopNotFoundError(["/path/1", "/path/2"])
        assert "/path/1" in error.reason
        assert "/path/2" in error.reason

    def test_has_download_suggestion(self):
        """Should suggest downloading Claude Desktop."""
        error = ClaudeDesktopNotFoundError(["/path"])
        assert any("claude.ai" in s.lower() for s in error.suggestions)


class TestProviderNotFoundError:
    """Tests for ProviderNotFoundError."""

    def test_includes_provider_name(self):
        """Error message should include provider name."""
        error = ProviderNotFoundError("my-provider")
        assert "my-provider" in str(error)

    def test_suggests_similar_providers(self):
        """Should suggest similar providers if provided."""
        error = ProviderNotFoundError("githb", similar=["github", "gitlab"])
        assert any("github" in s for s in error.suggestions)

    def test_has_search_suggestion(self):
        """Should suggest searching the registry."""
        error = ProviderNotFoundError("unknown")
        assert any("search" in s.lower() for s in error.suggestions)


class TestProviderStartError:
    """Tests for ProviderStartError."""

    def test_includes_provider_and_error(self):
        """Error should include provider name and error details."""
        error = ProviderStartError("my-provider", "Connection refused")
        assert "my-provider" in str(error)
        assert "Connection refused" in error.reason

    def test_exit_code_is_system_error(self):
        """System errors should have exit code 2."""
        error = ProviderStartError("provider", "error")
        assert error.exit_code == 2


class TestPermissionError:
    """Tests for PermissionError."""

    def test_includes_path_and_operation(self):
        """Error should include path and operation."""
        error = PermissionError("/path/to/file", "write")
        assert "/path/to/file" in str(error)
        assert "write" in str(error).lower() or "write" in error.reason.lower()
