"""CLI error handling for MCP Hangar.

Provides consistent error handling across all CLI commands with:
- Actionable error messages (what went wrong, why, what to do)
- Exit codes (0=success, 1=user error, 2=system error)
- Optional verbose mode for stack traces
"""

from dataclasses import dataclass, field

from rich.console import Console


@dataclass
class CLIError(Exception):
    """Base exception for CLI errors.

    Provides structured error information for user-friendly display.

    Attributes:
        message: What went wrong (short description)
        reason: Why it went wrong (explanation)
        suggestions: List of actions the user can take to fix it
        exit_code: Exit code to return (1=user error, 2=system error)
        original_error: The underlying exception, if any
    """

    message: str
    reason: str = ""
    suggestions: list[str] = field(default_factory=list)
    exit_code: int = 1
    original_error: Exception | None = None

    def __str__(self) -> str:
        return self.message


class ConfigNotFoundError(CLIError):
    """Raised when configuration file is not found."""

    def __init__(self, path: str):
        super().__init__(
            message=f"Configuration file not found: {path}",
            reason="The specified configuration file does not exist.",
            suggestions=[
                f"Create a config file at {path}",
                "Run 'mcp-hangar init' to create a default configuration",
                "Use --config to specify a different path",
            ],
            exit_code=1,
        )


class ClaudeDesktopNotFoundError(CLIError):
    """Raised when Claude Desktop installation is not found."""

    def __init__(self, searched_paths: list[str]):
        paths_str = "\n  - ".join(searched_paths)
        super().__init__(
            message="Claude Desktop installation not found",
            reason=f"Searched in:\n  - {paths_str}",
            suggestions=[
                "Install Claude Desktop from https://claude.ai/download",
                "Use --claude-config to specify the config location manually",
            ],
            exit_code=1,
        )


class ProviderNotFoundError(CLIError):
    """Raised when a provider is not found."""

    def __init__(self, provider_name: str, similar: list[str] | None = None):
        suggestions = [
            f"Run 'mcp-hangar add --search {provider_name}' to search the registry",
            "Run 'mcp-hangar status' to see available providers",
        ]
        if similar:
            suggestions.insert(0, f"Did you mean: {', '.join(similar)}?")

        super().__init__(
            message=f"Provider '{provider_name}' not found",
            reason="The provider is not installed or not available in the registry.",
            suggestions=suggestions,
            exit_code=1,
        )


class ProviderStartError(CLIError):
    """Raised when a provider fails to start."""

    def __init__(self, provider_name: str, error: str):
        super().__init__(
            message=f"Failed to start provider '{provider_name}'",
            reason=error,
            suggestions=[
                "Check that all required dependencies are installed",
                "Verify the provider configuration in your config.yaml",
                "Run with --verbose for more details",
            ],
            exit_code=2,
        )


class NetworkError(CLIError):
    """Raised when a network operation fails."""

    def __init__(self, operation: str, url: str, error: str):
        super().__init__(
            message=f"Network error during {operation}",
            reason=f"Failed to connect to {url}: {error}",
            suggestions=[
                "Check your internet connection",
                "Verify the URL is correct",
                "Try again in a few moments",
            ],
            exit_code=2,
        )


class InvalidConfigError(CLIError):
    """Raised when configuration is invalid."""

    def __init__(self, path: str, error: str):
        super().__init__(
            message=f"Invalid configuration in {path}",
            reason=error,
            suggestions=[
                "Check the configuration file syntax (YAML/JSON)",
                "See https://docs.mcp-hangar.io/configuration for reference",
                "Run 'mcp-hangar init --reset' to create a fresh config",
            ],
            exit_code=1,
        )


class PermissionError(CLIError):
    """Raised when there's a permission issue."""

    def __init__(self, path: str, operation: str):
        super().__init__(
            message=f"Permission denied: cannot {operation} {path}",
            reason="You don't have the necessary permissions for this operation.",
            suggestions=[
                f"Check permissions on {path}",
                "Run with elevated permissions if appropriate",
                "Choose a different location with --config",
            ],
            exit_code=1,
        )


def handle_cli_error(error: CLIError, console: Console) -> None:
    """Display a CLI error in a user-friendly format.

    Args:
        error: The CLIError to display
        console: Rich console to output to
    """
    # Build error content
    content_parts = []

    # Main error message
    content_parts.append(f"[bold red]Error:[/bold red] {error.message}")

    # Reason (why it happened)
    if error.reason:
        content_parts.append(f"\n{error.reason}")

    # Suggestions (what to do)
    if error.suggestions:
        content_parts.append("\n[bold]To fix:[/bold]")
        for i, suggestion in enumerate(error.suggestions, 1):
            content_parts.append(f"  {i}. {suggestion}")

    # Original error for verbose mode
    if error.original_error:
        content_parts.append(f"\n[dim]Original error: {error.original_error}[/dim]")

    console.print("\n".join(content_parts))


def format_success(message: str) -> str:
    """Format a success message."""
    return f"[green]{message}[/green]"


def format_warning(message: str) -> str:
    """Format a warning message."""
    return f"[yellow]{message}[/yellow]"


def format_info(message: str) -> str:
    """Format an info message."""
    return f"[blue]{message}[/blue]"


__all__ = [
    "CLIError",
    "ConfigNotFoundError",
    "ClaudeDesktopNotFoundError",
    "ProviderNotFoundError",
    "ProviderStartError",
    "NetworkError",
    "InvalidConfigError",
    "PermissionError",
    "handle_cli_error",
    "format_success",
    "format_warning",
    "format_info",
]
