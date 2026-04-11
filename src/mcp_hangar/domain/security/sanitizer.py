"""
Sanitizer module for MCP Hangar.

Provides sanitization utilities for:
- Command arguments (prevent injection)
- Environment variables (prevent injection)
- Log messages (prevent log injection)
- File paths (prevent traversal)
"""

import html
import re
from typing import Any
import unicodedata

# Characters that could enable injection attacks
SHELL_METACHARACTERS = set(";&|`$(){}[]<>!#*?~\n\r\t\0\\'\"")

# Patterns for control characters and dangerous sequences
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
NEWLINE_PATTERN = re.compile(r"[\r\n]")
NULL_BYTE_PATTERN = re.compile(r"\x00")
PATH_TRAVERSAL_PATTERN = re.compile(r"\.\.[\\/]")


class Sanitizer:
    """
    Comprehensive sanitizer for security-critical operations.

    Provides methods to sanitize various types of inputs to prevent
    injection attacks, log injection, and path traversal.
    """

    # Configurable limits
    MAX_ARGUMENT_LENGTH = 4096
    MAX_PATH_LENGTH = 4096
    MAX_LOG_MESSAGE_LENGTH = 10000
    MAX_ENV_VALUE_LENGTH = 32768

    def __init__(
        self,
        max_argument_length: int = MAX_ARGUMENT_LENGTH,
        max_path_length: int = MAX_PATH_LENGTH,
        max_log_message_length: int = MAX_LOG_MESSAGE_LENGTH,
    ):
        """
        Initialize sanitizer with configuration.

        Args:
            max_argument_length: Maximum length for command arguments
            max_path_length: Maximum length for file paths
            max_log_message_length: Maximum length for log messages
        """
        self.max_argument_length = max_argument_length
        self.max_path_length = max_path_length
        self.max_log_message_length = max_log_message_length

    def sanitize_command_argument(
        self,
        argument: str,
        allow_spaces: bool = True,
        allow_quotes: bool = False,
        replacement: str = "_",
    ) -> str:
        """
        Sanitize a command-line argument to prevent shell injection.

        This method removes or replaces characters that could be interpreted
        by the shell as metacharacters.

        Args:
            argument: The argument to sanitize
            allow_spaces: Whether to allow space characters
            allow_quotes: Whether to allow quote characters
            replacement: Character to replace dangerous chars with

        Returns:
            Sanitized argument string

        Note:
            This is a defense-in-depth measure. Always use subprocess with
            shell=False and pass arguments as a list.
        """
        if not isinstance(argument, str):
            argument = str(argument)

        # Truncate if too long
        if len(argument) > self.max_argument_length:
            argument = argument[: self.max_argument_length]

        # Remove null bytes
        argument = argument.replace("\0", "")

        # Remove control characters
        argument = CONTROL_CHAR_PATTERN.sub(replacement, argument)

        # Build set of allowed metacharacters
        dangerous = SHELL_METACHARACTERS.copy()
        if allow_spaces:
            dangerous.discard(" ")
        if allow_quotes:
            dangerous.discard('"')
            dangerous.discard("'")

        # Replace dangerous characters
        result = []
        for char in argument:
            if char in dangerous:
                result.append(replacement)
            else:
                result.append(char)

        return "".join(result)

    def sanitize_command_list(
        self,
        command: list[str],
        allow_spaces: bool = True,
    ) -> list[str]:
        """
        Sanitize an entire command list.

        Args:
            command: List of command arguments
            allow_spaces: Whether to allow spaces in arguments

        Returns:
            List of sanitized arguments
        """
        return [self.sanitize_command_argument(arg, allow_spaces=allow_spaces) for arg in command]

    def sanitize_environment_value(
        self,
        value: str,
        allow_newlines: bool = False,
    ) -> str:
        """
        Sanitize an environment variable value.

        Args:
            value: The value to sanitize
            allow_newlines: Whether to allow newline characters

        Returns:
            Sanitized value string
        """
        if not isinstance(value, str):
            value = str(value)

        # Truncate if too long
        if len(value) > self.MAX_ENV_VALUE_LENGTH:
            value = value[: self.MAX_ENV_VALUE_LENGTH]

        # Remove null bytes (always dangerous)
        value = value.replace("\0", "")

        # Optionally remove newlines
        if not allow_newlines:
            value = NEWLINE_PATTERN.sub(" ", value)

        return value

    def sanitize_environment_dict(
        self,
        env: dict[str, str],
        allow_newlines: bool = False,
    ) -> dict[str, str]:
        """
        Sanitize all environment variable values in a dictionary.

        Args:
            env: Dictionary of environment variables
            allow_newlines: Whether to allow newlines in values

        Returns:
            Dictionary with sanitized values
        """
        return {key: self.sanitize_environment_value(value, allow_newlines) for key, value in env.items()}

    def sanitize_path(
        self,
        path: str,
        allow_absolute: bool = False,
        allow_hidden: bool = True,
    ) -> str:
        """
        Sanitize a file path to prevent path traversal attacks.

        Args:
            path: The path to sanitize
            allow_absolute: Whether to allow absolute paths
            allow_hidden: Whether to allow hidden files (starting with .)

        Returns:
            Sanitized path string

        Raises:
            ValueError: If the path is invalid or dangerous
        """
        if not isinstance(path, str):
            path = str(path)

        # Truncate if too long
        if len(path) > self.max_path_length:
            raise ValueError(f"Path exceeds maximum length ({self.max_path_length})")

        # Remove null bytes
        path = path.replace("\0", "")

        # Normalize unicode to detect obfuscation
        path = unicodedata.normalize("NFKC", path)

        # Check for path traversal
        if PATH_TRAVERSAL_PATTERN.search(path) or ".." in path.split("/"):
            raise ValueError("Path contains traversal sequences")

        # Check for absolute paths
        if not allow_absolute:
            if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
                raise ValueError("Absolute paths are not allowed")

        # Check for hidden files
        if not allow_hidden:
            parts = path.replace("\\", "/").split("/")
            for part in parts:
                if part.startswith(".") and part not in (".", ".."):
                    raise ValueError("Hidden files/directories are not allowed")

        # Check for control characters
        if CONTROL_CHAR_PATTERN.search(path):
            raise ValueError("Path contains control characters")

        return path

    def sanitize_log_message(
        self,
        message: str,
        max_length: int | None = None,
    ) -> str:
        """
        Sanitize a message for safe logging.

        Prevents log injection attacks by:
        - Escaping newlines
        - Escaping control characters
        - Truncating long messages
        - Normalizing unicode

        Args:
            message: The message to sanitize
            max_length: Optional override for max length

        Returns:
            Sanitized message string
        """
        if not isinstance(message, str):
            message = str(message)

        max_len = max_length or self.max_log_message_length

        # Truncate if too long
        if len(message) > max_len:
            message = message[:max_len] + "...[truncated]"

        # Normalize unicode
        message = unicodedata.normalize("NFKC", message)

        # Replace newlines with visible escape sequences
        message = message.replace("\r\n", "\\r\\n")
        message = message.replace("\r", "\\r")
        message = message.replace("\n", "\\n")

        # Replace other control characters with their escape representation
        def escape_control(match):
            char = match.group(0)
            if char == "\t":
                return "\\t"
            return f"\\x{ord(char):02x}"

        message = CONTROL_CHAR_PATTERN.sub(escape_control, message)

        return message

    def sanitize_for_json(self, value: Any) -> Any:
        """
        Sanitize a value for safe JSON serialization.

        Handles nested structures recursively.

        Args:
            value: The value to sanitize

        Returns:
            Sanitized value safe for JSON
        """
        if isinstance(value, str):
            # Remove null bytes and control chars except common whitespace
            result = value.replace("\0", "")
            # Keep tabs and newlines but remove other control chars
            result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", result)
            return result
        elif isinstance(value, dict):
            return {self.sanitize_for_json(k): self.sanitize_for_json(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self.sanitize_for_json(item) for item in value]
        elif isinstance(value, int | float | bool | type(None)):
            return value
        else:
            # Convert unknown types to string and sanitize
            return self.sanitize_for_json(str(value))

    def escape_html(self, value: str) -> str:
        """
        Escape HTML special characters.

        Args:
            value: The string to escape

        Returns:
            HTML-escaped string
        """
        return html.escape(value, quote=True)

    def mask_value(
        self,
        value: str,
        visible_chars: int = 4,
        mask_char: str = "*",
    ) -> str:
        """
        Mask a sensitive value, showing only first few characters.

        Args:
            value: The value to mask
            visible_chars: Number of characters to show at the start
            mask_char: Character to use for masking

        Returns:
            Masked string
        """
        if not value:
            return ""

        if len(value) <= visible_chars:
            return mask_char * len(value)

        return value[:visible_chars] + mask_char * (len(value) - visible_chars)


# --- Convenience Functions ---

# Global sanitizer instance with default settings
_default_sanitizer = Sanitizer()


def sanitize_command_argument(
    argument: str,
    allow_spaces: bool = True,
    allow_quotes: bool = False,
) -> str:
    """Sanitize a command argument using default sanitizer."""
    return _default_sanitizer.sanitize_command_argument(argument, allow_spaces, allow_quotes)


def sanitize_environment_value(
    value: str,
    allow_newlines: bool = False,
) -> str:
    """Sanitize an environment value using default sanitizer."""
    return _default_sanitizer.sanitize_environment_value(value, allow_newlines)


def sanitize_log_message(
    message: str,
    max_length: int | None = None,
) -> str:
    """Sanitize a log message using default sanitizer."""
    return _default_sanitizer.sanitize_log_message(message, max_length)


def sanitize_path(
    path: str,
    allow_absolute: bool = False,
    allow_hidden: bool = True,
) -> str:
    """Sanitize a file path using default sanitizer."""
    return _default_sanitizer.sanitize_path(path, allow_absolute, allow_hidden)
