"""Output redactor for sensitive information.

This module provides functionality to redact sensitive information from
output text, logs, and error messages to prevent secret leakage.
"""

from dataclasses import dataclass
import re


@dataclass
class RedactionPattern:
    """A pattern for detecting and redacting sensitive information.

    Attributes:
        pattern: Compiled regex pattern.
        name: Human-readable name for the pattern.
        replacement: Optional custom replacement string.
    """

    pattern: re.Pattern
    name: str
    replacement: str | None = None


class OutputRedactor:
    """Redacts sensitive information from text output.

    Detects and redacts:
    - Known secret values (from environment/config)
    - Common API key patterns (Stripe, GitHub, Slack, etc.)
    - Bearer tokens
    - Long alphanumeric strings (potential secrets)

    Attributes:
        known_secrets: Dictionary mapping secret names to their values.
    """

    BUILTIN_PATTERNS = [
        RedactionPattern(
            pattern=re.compile(r"sk_live_[a-zA-Z0-9]{24,}"),
            name="stripe_live_key",
        ),
        RedactionPattern(
            pattern=re.compile(r"sk_test_[a-zA-Z0-9]{24,}"),
            name="stripe_test_key",
        ),
        RedactionPattern(
            pattern=re.compile(r"rk_live_[a-zA-Z0-9]{24,}"),
            name="stripe_restricted_key",
        ),
        RedactionPattern(
            pattern=re.compile(r"rk_test_[a-zA-Z0-9]{24,}"),
            name="stripe_restricted_test_key",
        ),
        RedactionPattern(
            pattern=re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
            name="github_pat",
        ),
        RedactionPattern(
            pattern=re.compile(r"gho_[a-zA-Z0-9]{36,}"),
            name="github_oauth",
        ),
        RedactionPattern(
            pattern=re.compile(r"ghu_[a-zA-Z0-9]{36,}"),
            name="github_user_token",
        ),
        RedactionPattern(
            pattern=re.compile(r"ghs_[a-zA-Z0-9]{36,}"),
            name="github_server_token",
        ),
        RedactionPattern(
            pattern=re.compile(r"ghr_[a-zA-Z0-9]{36,}"),
            name="github_refresh_token",
        ),
        RedactionPattern(
            pattern=re.compile(r"github_pat_[a-zA-Z0-9_]{82}"),
            name="github_fine_grained_pat",
        ),
        RedactionPattern(
            pattern=re.compile(r"xox[baprs]-[a-zA-Z0-9-]{10,}"),
            name="slack_token",
        ),
        RedactionPattern(
            pattern=re.compile(r"Bearer\s+[a-zA-Z0-9._\-]{20,}", re.IGNORECASE),
            name="bearer_token",
            replacement="Bearer [REDACTED]",
        ),
        RedactionPattern(
            pattern=re.compile(r"Authorization:\s*Bearer\s+[a-zA-Z0-9._\-]{20,}", re.IGNORECASE),
            name="auth_header",
            replacement="Authorization: Bearer [REDACTED]",
        ),
        RedactionPattern(
            pattern=re.compile(r"api[_-]?key[=:]\s*[a-zA-Z0-9._\-]{20,}", re.IGNORECASE),
            name="generic_api_key",
            replacement="api_key=[REDACTED]",
        ),
        RedactionPattern(
            pattern=re.compile(r"AKIA[A-Z0-9]{16}"),
            name="aws_access_key",
        ),
        RedactionPattern(
            pattern=re.compile(r"AIza[0-9A-Za-z_-]{35}"),
            name="google_api_key",
        ),
        RedactionPattern(
            pattern=re.compile(r"eyJ[a-zA-Z0-9_-]{50,}\.[a-zA-Z0-9_-]{50,}"),
            name="jwt_token",
        ),
        RedactionPattern(
            pattern=re.compile(r"npm_[a-zA-Z0-9]{36}"),
            name="npm_token",
        ),
        RedactionPattern(
            pattern=re.compile(r"pypi-[a-zA-Z0-9_-]{100,}"),
            name="pypi_token",
        ),
    ]

    LONG_SECRET_PATTERN = re.compile(r"[a-zA-Z0-9_\-]{32,}")

    def __init__(
        self,
        known_secrets: dict[str, str] | None = None,
        redact_long_strings: bool = True,
        min_long_string_length: int = 32,
    ):
        """Initialize the output redactor.

        Args:
            known_secrets: Dictionary mapping secret names to their values.
            redact_long_strings: Whether to redact long alphanumeric strings.
            min_long_string_length: Minimum length for long string redaction.
        """
        self._known_secrets = known_secrets or {}
        self._redact_long_strings = redact_long_strings
        self._min_long_string_length = min_long_string_length
        self._custom_patterns: list[RedactionPattern] = []

    def add_known_secret(self, name: str, value: str) -> None:
        """Add a known secret to be redacted.

        Args:
            name: Name of the secret (used in replacement).
            value: The secret value to redact.
        """
        if value and len(value) >= 4:
            self._known_secrets[name] = value

    def add_pattern(self, pattern: re.Pattern | str, name: str, replacement: str | None = None) -> None:
        """Add a custom redaction pattern.

        Args:
            pattern: Regex pattern or string to compile.
            name: Human-readable name for the pattern.
            replacement: Optional custom replacement string.
        """
        if isinstance(pattern, str):
            pattern = re.compile(pattern)
        self._custom_patterns.append(RedactionPattern(pattern, name, replacement))

    def redact(self, text: str) -> str:
        """Redact sensitive information from text.

        Args:
            text: The text to redact.

        Returns:
            Text with sensitive information replaced with [REDACTED:name].
        """
        if not text:
            return text

        result = text

        for name, value in self._known_secrets.items():
            if value and value in result:
                result = result.replace(value, f"[REDACTED:{name}]")

        for pattern in self.BUILTIN_PATTERNS:
            if pattern.replacement:
                result = pattern.pattern.sub(pattern.replacement, result)
            else:
                result = pattern.pattern.sub(f"[REDACTED:{pattern.name}]", result)

        for pattern in self._custom_patterns:
            if pattern.replacement:
                result = pattern.pattern.sub(pattern.replacement, result)
            else:
                result = pattern.pattern.sub(f"[REDACTED:{pattern.name}]", result)

        if self._redact_long_strings:
            result = self._redact_long_strings_fn(result)

        return result

    def _redact_long_strings_fn(self, text: str) -> str:
        """Redact long alphanumeric strings that might be secrets.

        Args:
            text: The text to process.

        Returns:
            Text with long strings redacted.
        """

        def replace_if_suspicious(match: re.Match) -> str:
            value = match.group(0)
            if len(value) < self._min_long_string_length:
                return value
            if value in self._known_secrets.values():
                return value
            if self._looks_like_code(value):
                return value
            if self._is_known_safe_pattern(value):
                return value
            return "[REDACTED:potential_secret]"

        return self.LONG_SECRET_PATTERN.sub(replace_if_suspicious, text)

    def _looks_like_code(self, value: str) -> bool:
        """Check if a string looks like code rather than a secret.

        Args:
            value: The string to check.

        Returns:
            True if the string looks like code.
        """
        if "_" in value and value == value.upper():
            return True

        if value.startswith("get_") or value.startswith("set_"):
            return True
        if value.endswith("_test") or value.endswith("_spec"):
            return True

        if value.startswith("test_") or value.startswith("spec_"):
            return True

        return False

    def _is_known_safe_pattern(self, value: str) -> bool:
        """Check if a string matches a known safe pattern.

        Args:
            value: The string to check.

        Returns:
            True if the string is known to be safe.
        """
        safe_prefixes = [
            "sha256-",
            "sha512-",
            "md5-",
            "base64-",
            "uuid-",
            "00000000-0000-0000-0000-",
        ]
        for prefix in safe_prefixes:
            if value.lower().startswith(prefix):
                return True

        if len(set(value)) <= 3:
            return True

        return False

    def is_sensitive(self, text: str) -> bool:
        """Check if text contains sensitive information.

        Args:
            text: The text to check.

        Returns:
            True if the text contains sensitive information.
        """
        if not text:
            return False

        for value in self._known_secrets.values():
            if value and value in text:
                return True

        for pattern in self.BUILTIN_PATTERNS:
            if pattern.pattern.search(text):
                return True

        for pattern in self._custom_patterns:
            if pattern.pattern.search(text):
                return True

        return False
