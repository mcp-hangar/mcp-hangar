"""
Secrets management for MCP Hangar.

Provides secure handling of sensitive data:
- Masking sensitive values in logs
- Secure environment variable handling
- Detection of sensitive keys
- Redaction utilities
"""

import builtins
from dataclasses import dataclass, field
import os
import re
from re import Pattern
from typing import Any

# Patterns that indicate a key might contain sensitive data
SENSITIVE_KEY_PATTERNS: list[Pattern] = [
    re.compile(r"(?i)password"),
    re.compile(r"(?i)passwd"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)apikey"),
    re.compile(r"(?i)auth[_-]?token"),
    re.compile(r"(?i)access[_-]?token"),
    re.compile(r"(?i)bearer"),
    re.compile(r"(?i)credential"),
    re.compile(r"(?i)private[_-]?key"),
    re.compile(r"(?i)ssh[_-]?key"),
    re.compile(r"(?i)encryption[_-]?key"),
    re.compile(r"(?i)signing[_-]?key"),
    re.compile(r"(?i)client[_-]?secret"),
    re.compile(r"(?i)db[_-]?pass"),
    re.compile(r"(?i)database[_-]?password"),
    re.compile(r"(?i)connection[_-]?string"),
    re.compile(r"(?i)conn[_-]?str"),
    re.compile(r"(?i)jwt"),
    re.compile(r"(?i)session[_-]?id"),
    re.compile(r"(?i)cookie"),
    re.compile(r"(?i)oauth"),
    re.compile(r"(?i)_token$"),
    re.compile(r"(?i)_key$"),
    re.compile(r"(?i)_secret$"),
]

# Exact key names that are always considered sensitive
SENSITIVE_KEYS: frozenset[str] = frozenset(
    [
        "PASSWORD",
        "PASSWD",
        "SECRET",
        "API_KEY",
        "APIKEY",
        "AUTH_TOKEN",
        "ACCESS_TOKEN",
        "REFRESH_TOKEN",
        "BEARER_TOKEN",
        "PRIVATE_KEY",
        "SSH_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_CLIENT_SECRET",
        "GCP_PRIVATE_KEY",
        "GITHUB_TOKEN",
        "GITLAB_TOKEN",
        "NPM_TOKEN",
        "PYPI_TOKEN",
        "DATABASE_URL",
        "REDIS_URL",
        "MONGODB_URI",
        "POSTGRES_PASSWORD",
        "MYSQL_PASSWORD",
        "JWT_SECRET",
        "ENCRYPTION_KEY",
        "SIGNING_KEY",
        "MASTER_KEY",
    ]
)


def is_sensitive_key(key: str) -> bool:
    """
    Check if a key name likely contains sensitive data.

    Args:
        key: The key name to check

    Returns:
        True if the key is likely sensitive
    """
    if not key:
        return False

    # Check exact matches (case-insensitive)
    if key.upper() in SENSITIVE_KEYS:
        return True

    # Check patterns
    for pattern in SENSITIVE_KEY_PATTERNS:
        if pattern.search(key):
            return True

    return False


def mask_sensitive_value(
    value: str,
    visible_prefix: int = 4,
    visible_suffix: int = 0,
    mask_char: str = "*",
    min_mask_length: int = 8,
    max_visible: int = 8,
) -> str:
    """
    Mask a sensitive value, optionally showing prefix/suffix.

    Args:
        value: The value to mask
        visible_prefix: Number of characters to show at the start
        visible_suffix: Number of characters to show at the end
        mask_char: Character to use for masking
        min_mask_length: Minimum number of mask characters to show
        max_visible: Maximum total visible characters

    Returns:
        Masked string
    """
    if not value:
        return ""

    # For very short values, mask everything
    if len(value) <= min_mask_length:
        return mask_char * len(value)

    # Limit visible characters
    total_visible = visible_prefix + visible_suffix
    if total_visible > max_visible:
        # Reduce proportionally
        ratio = max_visible / total_visible
        visible_prefix = int(visible_prefix * ratio)
        visible_suffix = int(visible_suffix * ratio)
        total_visible = visible_prefix + visible_suffix

    # Ensure we have enough characters to mask
    if len(value) <= total_visible + min_mask_length:
        # Show less prefix
        visible_prefix = max(0, len(value) - min_mask_length - visible_suffix)

    # Calculate mask length
    mask_length = max(min_mask_length, len(value) - visible_prefix - visible_suffix)

    # Build the masked string
    parts = []
    if visible_prefix > 0:
        parts.append(value[:visible_prefix])
    parts.append(mask_char * mask_length)
    if visible_suffix > 0 and len(value) > visible_prefix + visible_suffix:
        parts.append(value[-visible_suffix:])

    return "".join(parts)


@dataclass
class SecretsMask:
    """
    Configuration for masking secrets in various contexts.
    """

    # Characters to show at start of masked values
    visible_prefix: int = 4

    # Characters to show at end of masked values
    visible_suffix: int = 0

    # Character used for masking
    mask_char: str = "*"

    # Minimum mask length
    min_mask_length: int = 8

    # Additional patterns to consider sensitive
    additional_patterns: list[Pattern] = field(default_factory=list)

    # Additional exact keys to consider sensitive
    additional_keys: set[str] = field(default_factory=set)

    # Keys to never mask (override)
    safe_keys: set[str] = field(default_factory=set)

    def is_sensitive(self, key: str) -> bool:
        """Check if a key is sensitive according to this configuration."""
        if key in self.safe_keys:
            return False

        if key.upper() in self.additional_keys:
            return True

        for pattern in self.additional_patterns:
            if pattern.search(key):
                return True

        return is_sensitive_key(key)

    def mask(self, value: str) -> str:
        """Mask a value according to this configuration."""
        return mask_sensitive_value(
            value,
            visible_prefix=self.visible_prefix,
            visible_suffix=self.visible_suffix,
            mask_char=self.mask_char,
            min_mask_length=self.min_mask_length,
        )

    def mask_dict(
        self,
        data: dict[str, Any],
        recursive: bool = True,
    ) -> dict[str, Any]:
        """
        Mask sensitive values in a dictionary.

        Args:
            data: Dictionary to mask
            recursive: Whether to process nested dictionaries

        Returns:
            New dictionary with sensitive values masked
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, dict) and recursive:
                result[key] = self.mask_dict(value, recursive=True)
            elif isinstance(value, str) and self.is_sensitive(key):
                result[key] = self.mask(value)
            else:
                result[key] = value
        return result


class SecureEnvironment:
    """
    Secure wrapper for environment variable handling.

    Provides safe access to environment variables with automatic
    masking of sensitive values in logs and error messages.
    """

    def __init__(
        self,
        env: dict[str, str] | None = None,
        mask_config: SecretsMask | None = None,
    ):
        """
        Initialize secure environment.

        Args:
            env: Dictionary of environment variables (defaults to os.environ)
            mask_config: Configuration for masking (uses defaults if not provided)
        """
        self._env = dict(env) if env is not None else dict(os.environ)
        self._mask = mask_config or SecretsMask()
        self._accessed_keys: set[str] = set()

    def get(
        self,
        key: str,
        default: str | None = None,
        required: bool = False,
    ) -> str | None:
        """
        Get an environment variable value.

        Args:
            key: Environment variable name
            default: Default value if not found
            required: If True, raise error if not found

        Returns:
            The value or default

        Raises:
            KeyError: If required and not found
        """
        self._accessed_keys.add(key)

        if key in self._env:
            return self._env[key]

        if required:
            raise KeyError(f"Required environment variable not set: {key}")

        return default

    def get_masked(
        self,
        key: str,
        default: str | None = None,
    ) -> str | None:
        """
        Get an environment variable value, masked if sensitive.

        Args:
            key: Environment variable name
            default: Default value if not found

        Returns:
            The value (masked if sensitive) or default
        """
        value = self.get(key, default)
        if value is None:
            return None

        if self._mask.is_sensitive(key):
            return self._mask.mask(value)

        return value

    def set(self, key: str, value: str) -> None:
        """
        Set an environment variable.

        Args:
            key: Environment variable name
            value: Value to set
        """
        self._env[key] = value

    def unset(self, key: str) -> None:
        """
        Remove an environment variable.

        Args:
            key: Environment variable name
        """
        self._env.pop(key, None)

    def to_dict(self, mask_sensitive: bool = True) -> dict[str, str]:
        """
        Export environment as dictionary.

        Args:
            mask_sensitive: Whether to mask sensitive values

        Returns:
            Dictionary of environment variables
        """
        if not mask_sensitive:
            return dict(self._env)

        return self._mask.mask_dict(self._env, recursive=False)

    def to_subprocess_env(
        self,
        include_parent: bool = True,
        whitelist: builtins.set[str] | None = None,
        blacklist: builtins.set[str] | None = None,
    ) -> dict[str, str]:
        """
        Prepare environment for subprocess execution.

        Args:
            include_parent: Whether to include parent process environment
            whitelist: If set, only include these keys
            blacklist: Keys to exclude

        Returns:
            Dictionary suitable for subprocess env parameter
        """
        if include_parent:
            result = dict(os.environ)
            result.update(self._env)
        else:
            result = dict(self._env)

        # Apply whitelist
        if whitelist is not None:
            result = {k: v for k, v in result.items() if k in whitelist}

        # Apply blacklist
        if blacklist is not None:
            result = {k: v for k, v in result.items() if k not in blacklist}

        return result

    def validate(self, required_keys: list[str]) -> list[str]:
        """
        Validate that required environment variables are set.

        Args:
            required_keys: List of required key names

        Returns:
            List of missing key names (empty if all present)
        """
        missing = []
        for key in required_keys:
            if key not in self._env or not self._env[key]:
                missing.append(key)
        return missing

    @property
    def accessed_keys(self) -> builtins.set[str]:
        """Get the set of keys that have been accessed."""
        return self._accessed_keys.copy()

    def __contains__(self, key: str) -> bool:
        """Check if a key exists."""
        return key in self._env

    def __repr__(self) -> str:
        """Safe representation that doesn't expose secrets."""
        keys = sorted(self._env.keys())
        return f"SecureEnvironment({len(keys)} variables)"


# --- Utility Functions ---


def redact_secrets_in_string(
    text: str,
    patterns: list[Pattern] | None = None,
    replacement: str = "[REDACTED]",
) -> str:
    """
    Redact potential secrets in a string.

    Useful for sanitizing log messages or error output.

    Args:
        text: The text to redact
        patterns: Custom patterns to match (uses defaults if not provided)
        replacement: Replacement text for redacted values

    Returns:
        Text with secrets redacted
    """
    # Common patterns that might contain secrets in text
    default_patterns = [
        # API keys (various formats)
        re.compile(r'(?i)(api[_-]?key|apikey)["\s:=]+["\']?([a-zA-Z0-9_\-]{16,})'),
        # Bearer tokens
        re.compile(r"(?i)bearer\s+([a-zA-Z0-9_\-\.]+)"),
        # Basic auth in URLs
        re.compile(r"://([^:]+):([^@]+)@"),
        # Password in query strings
        re.compile(r"(?i)password=([^&\s]+)"),
        # Secret in query strings
        re.compile(r"(?i)secret=([^&\s]+)"),
        # Token in query strings
        re.compile(r"(?i)token=([^&\s]+)"),
        # AWS keys
        re.compile(r"(?i)(AKIA[A-Z0-9]{16})"),
        # Private keys
        re.compile(r"-----BEGIN[A-Z\s]+PRIVATE KEY-----"),
    ]

    patterns_to_use = patterns or default_patterns
    result = text

    for pattern in patterns_to_use:
        result = pattern.sub(replacement, result)

    return result


def create_secure_env_for_provider(
    base_env: dict[str, str] | None = None,
    provider_env: dict[str, str] | None = None,
    inherit_parent: bool = True,
    sensitive_key_filter: bool = True,
) -> SecureEnvironment:
    """
    Create a secure environment for provider execution.

    Args:
        base_env: Base environment variables
        provider_env: Provider-specific environment variables
        inherit_parent: Whether to inherit from parent process
        sensitive_key_filter: Whether to filter out inherited sensitive keys

    Returns:
        SecureEnvironment configured for provider use
    """
    env_dict: dict[str, str] = {}

    # Start with parent environment if requested
    if inherit_parent:
        # Filter sensitive keys from parent env if requested
        for key, value in os.environ.items():
            if sensitive_key_filter and is_sensitive_key(key):
                continue
            env_dict[key] = value

    # Add base environment (overrides parent)
    if base_env:
        env_dict.update(base_env)

    # Add provider-specific environment (overrides all)
    if provider_env:
        env_dict.update(provider_env)

    return SecureEnvironment(env_dict)
