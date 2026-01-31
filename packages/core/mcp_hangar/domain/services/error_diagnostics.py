"""Error diagnostics service for provider startup failures.

Provides:
- get_suggestion_for_error: Generate actionable suggestions based on error patterns
- collect_startup_diagnostics: Collect diagnostic information from failed processes
"""

from typing import Any

# Error patterns to match against stderr (pattern, suggestion)
# Patterns are matched in order, first match wins
_STDERR_PATTERNS: list[tuple[str, str]] = [
    # Python errors
    ("modulenotfounderror", "Install missing Python dependencies. Check your virtual environment is activated."),
    ("no module named", "Install missing Python dependencies. Check your virtual environment is activated."),
    ("importerror", "Check that all required packages are installed and import paths are correct."),
    ("syntaxerror", "Fix the syntax error in the provider code before starting."),
    # Permission/file errors
    ("permissionerror", "Check file permissions. Ensure the provider script is executable."),
    ("permission denied", "Check file permissions. Ensure the provider script is executable."),
    ("filenotfounderror", "Check that all referenced files and paths exist."),
    ("no such file or directory", "Check that all referenced files and paths exist."),
    # Network errors
    ("connectionrefused", "The target service is not running or not accepting connections."),
    ("connection refused", "The target service is not running or not accepting connections."),
    ("timeout", "The operation timed out. Check network connectivity and service availability."),
    # Memory errors
    ("out of memory", "The provider ran out of memory. Consider increasing memory limits."),
    ("memoryerror", "The provider ran out of memory. Consider increasing memory limits."),
    # MCP protocol errors
    ("jsonrpc", "MCP protocol error. Check that the provider implements the MCP protocol correctly."),
    ("json-rpc", "MCP protocol error. Check that the provider implements the MCP protocol correctly."),
]

# Container-specific patterns (require secondary pattern match)
_CONTAINER_PATTERNS: list[tuple[str, str, str]] = [
    # (container_keyword, secondary_pattern, suggestion)
    ("docker", "not found", "Ensure Docker/Podman is installed and running. Check that the image exists."),
    ("podman", "not found", "Ensure Docker/Podman is installed and running. Check that the image exists."),
    ("container", "not found", "Ensure Docker/Podman is installed and running. Check that the image exists."),
    ("docker", "permission", "Check Docker/Podman permissions. You may need to add your user to the docker group."),
    ("podman", "permission", "Check Docker/Podman permissions. You may need to add your user to the docker group."),
]

# Exit code to suggestion mapping
_EXIT_CODE_SUGGESTIONS: dict[int, str] = {
    1: "General error. Check the provider logs for more details.",
    2: "Command line usage error. Verify the provider command and arguments.",
    126: "Command not executable. Check file permissions (chmod +x).",
    127: "Command not found. Check that the command exists and PATH is correct.",
    137: "Process was killed (OOM or SIGKILL). Consider increasing memory limits.",
    139: "Segmentation fault. This indicates a bug in the provider code.",
}


def get_suggestion_for_error(
    stderr: str | None,
    exit_code: int | None,
) -> str | None:
    """
    Generate actionable suggestion based on error patterns.

    Analyzes stderr content and exit codes to provide helpful guidance
    for troubleshooting provider startup failures.

    Args:
        stderr: Captured stderr output from the failed process
        exit_code: Process exit code (if available)

    Returns:
        Actionable suggestion string, or None if no pattern matched
    """
    if not stderr and exit_code is None:
        return None

    stderr_lower = (stderr or "").lower()

    # Check standard patterns
    for pattern, suggestion in _STDERR_PATTERNS:
        if pattern in stderr_lower:
            return suggestion

    # Check container-specific patterns (require two matches)
    for container_kw, secondary_pattern, suggestion in _CONTAINER_PATTERNS:
        if container_kw in stderr_lower and secondary_pattern in stderr_lower:
            return suggestion

    # Fall back to exit code suggestions
    if exit_code is not None:
        return _EXIT_CODE_SUGGESTIONS.get(exit_code)

    return None


def collect_startup_diagnostics(client: Any) -> dict[str, Any]:
    """
    Collect diagnostic information from a failed client/process.

    Extracts stderr output, exit code, and generates actionable suggestions
    based on error patterns detected.

    Args:
        client: The client object (StdioClient or similar) with a 'process' attribute

    Returns:
        Dictionary containing:
        - stderr: captured stderr output (if available)
        - exit_code: process exit code (if available)
        - suggestion: actionable suggestion based on error patterns
    """
    diagnostics: dict[str, Any] = {
        "stderr": None,
        "exit_code": None,
        "suggestion": None,
    }

    if client is None:
        return diagnostics

    proc = getattr(client, "process", None)
    if not proc:
        return diagnostics

    # Get exit code
    try:
        rc = proc.poll()
        if rc is not None:
            diagnostics["exit_code"] = rc
    except Exception:
        pass

    # Get stderr - prefer already captured by StdioClient
    last_stderr = getattr(client, "_last_stderr", None)
    if last_stderr:
        diagnostics["stderr"] = last_stderr
    else:
        # Fallback: try to read stderr directly
        stderr = getattr(proc, "stderr", None)
        if stderr:
            try:
                err_bytes = stderr.read()
                if err_bytes:
                    err_text = (err_bytes if isinstance(err_bytes, str) else err_bytes.decode(errors="replace")).strip()
                    if err_text:
                        diagnostics["stderr"] = err_text
            except Exception:
                pass

    # Generate suggestion based on error patterns
    diagnostics["suggestion"] = get_suggestion_for_error(
        diagnostics.get("stderr"),
        diagnostics.get("exit_code"),
    )

    return diagnostics
