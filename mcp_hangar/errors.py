"""Human-readable error classes with recovery hints.

This module provides rich error types that include:

- Clear user-facing messages
- Technical details for debugging
- Actionable recovery hints
- Related log references

Error output example::

    ProviderProtocolError: SQLite provider returned invalid response
      ↳ Provider: sqlite
      ↳ Operation: query
      ↳ Details: Expected JSON object, received plain text

    💡 Recovery steps:
      1. Retry the operation (often transient)
      2. Check provider logs: registry_details('sqlite')
      3. If persistent, file bug report

See docs/guides/UX_IMPROVEMENTS.md for usage examples.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class HangarError(Exception):
    """Base exception with rich context for better UX.

    All mcp-hangar errors inherit from this class, providing:
    - User-friendly error messages
    - Technical debugging information
    - Actionable recovery steps
    - Log file references when available
    """

    # User-facing
    message: str
    """Clear, non-technical explanation of what went wrong."""

    recovery_hints: List[str] = field(default_factory=list)
    """Actionable steps the user can take to resolve the issue."""

    # Technical context
    provider: str = ""
    """The provider that caused the error."""

    operation: str = ""
    """The operation that was being performed."""

    technical_details: str = ""
    """Technical error details for debugging."""

    # Debugging
    related_logs: Optional[str] = None
    """Path to relevant log entries (e.g., '/logs/mcp-hangar.log:580')."""

    issue_url: Optional[str] = None
    """Link to known issue or documentation."""

    original_exception: Optional[Exception] = None
    """The original exception that caused this error."""

    context: Dict[str, Any] = field(default_factory=dict)
    """Additional context data for debugging."""

    def __post_init__(self):
        super().__init__(self.message)

    def __str__(self) -> str:
        """Format error for display with recovery hints."""
        output = [
            f"\n{self.__class__.__name__}: {self.message}",
        ]

        if self.provider:
            output.append(f"  ↳ Provider: {self.provider}")
        if self.operation:
            output.append(f"  ↳ Operation: {self.operation}")
        if self.technical_details:
            output.append(f"  ↳ Details: {self.technical_details}")

        if self.recovery_hints:
            output.append("")
            output.append("💡 Recovery steps:")
            for i, hint in enumerate(self.recovery_hints, 1):
                output.append(f"  {i}. {hint}")

        if self.related_logs:
            output.append(f"\n📋 Related logs: {self.related_logs}")

        if self.issue_url:
            output.append(f"🔗 Known issue: {self.issue_url}")

        return "\n".join(output)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "provider": self.provider,
            "operation": self.operation,
            "technical_details": self.technical_details,
            "recovery_hints": self.recovery_hints,
            "related_logs": self.related_logs,
            "issue_url": self.issue_url,
            "context": self.context,
        }


@dataclass
class TransientError(HangarError):
    """Temporary failure that may succeed on retry.

    Examples:
    - Network glitch
    - Race condition during startup
    - Malformed JSON response (often recovers)

    The retry system will automatically retry these errors.
    """

    retryable: bool = True
    """Indicates this error can be retried."""

    suggested_delay: float = 1.0
    """Suggested delay before retry in seconds."""


@dataclass
class ProviderProtocolError(HangarError):
    """Provider violated MCP protocol.

    This occurs when a provider sends an invalid response,
    such as malformed JSON or unexpected data format.
    """

    raw_response: Optional[str] = None
    """Preview of the invalid response (truncated for safety)."""

    def __post_init__(self):
        if not self.recovery_hints:
            self.recovery_hints = [
                "Retry the operation (often transient)",
                f"Check provider logs: registry_details('{self.provider}')",
                "If persistent, file bug report with raw response",
            ]
        super().__post_init__()


@dataclass
class ProviderCrashError(HangarError):
    """Provider process terminated unexpectedly.

    This occurs when a provider process dies or is killed,
    either due to an internal error, resource limits, or idle timeout.
    """

    exit_code: Optional[int] = None
    """Process exit code if available."""

    signal_name: Optional[str] = None
    """Signal name if killed by signal (e.g., SIGKILL)."""

    idle_duration_s: Optional[float] = None
    """How long the provider was idle before shutdown (if applicable)."""

    def __post_init__(self):
        if not self.recovery_hints:
            hints = [
                "Provider will auto-restart on next use",
            ]
            if self.idle_duration_s is not None:
                hints.insert(0, f"Provider was idle for {self.idle_duration_s:.0f}s and forced shutdown")
                hints.append("This is normal behavior for idle providers")
                hints.append("If frequent, increase idle_ttl_s in config")
            else:
                hints.append(f"Check provider logs: registry_details('{self.provider}')")
                hints.append("Check for memory/resource issues in container")
            self.recovery_hints = hints
        super().__post_init__()


@dataclass
class NetworkError(HangarError):
    """Network connectivity issue.

    This occurs when the hangar cannot reach a remote provider,
    due to DNS issues, firewall rules, or network outages.
    """

    hostname: Optional[str] = None
    """The hostname that was unreachable."""

    error_code: Optional[str] = None
    """Network error code (e.g., EAI_AGAIN for DNS)."""

    def __post_init__(self):
        if not self.recovery_hints:
            hints = []
            if self.hostname:
                hints.append(f"Check network connectivity: ping {self.hostname}")
                hints.append(f"Verify DNS resolution: nslookup {self.hostname}")
            hints.append("Try with longer timeout: timeout=60")
            hints.append("Check firewall/proxy settings")
            self.recovery_hints = hints
        super().__post_init__()


@dataclass
class ConfigurationError(HangarError):
    """User configuration problem.

    This occurs when there's an issue with the configuration file,
    missing required settings, or invalid values.
    """

    config_path: Optional[str] = None
    """Path to the configuration file."""

    field_name: Optional[str] = None
    """Name of the problematic configuration field."""

    def __post_init__(self):
        if not self.recovery_hints:
            hints = []
            if self.config_path:
                hints.append(f"Check config file at: {self.config_path}")
            if self.field_name:
                hints.append(f"Review the '{self.field_name}' setting")
            hints.append("Use registry_discover() to auto-detect providers")
            hints.append("Check example config: docs/configuration.md")
            self.recovery_hints = hints
        super().__post_init__()


@dataclass
class ProviderNotFoundError(HangarError):
    """Provider not found in registry.

    The specified provider ID doesn't exist in the configuration.
    """

    available_providers: List[str] = field(default_factory=list)
    """List of available provider IDs."""

    def __post_init__(self):
        if not self.recovery_hints:
            hints = [
                "Use registry_list() to see available providers",
            ]
            if self.available_providers:
                similar = self._find_similar()
                if similar:
                    hints.append(f"Did you mean: {similar}?")
            hints.append("Add provider to config.yaml")
            self.recovery_hints = hints
        super().__post_init__()

    def _find_similar(self) -> Optional[str]:
        """Find similar provider name for 'did you mean' suggestion."""
        if not self.provider or not self.available_providers:
            return None

        target = self.provider.lower()
        best_match = None
        best_score = 0

        for name in self.available_providers:
            name_lower = name.lower()
            # Simple substring matching
            if target in name_lower or name_lower in target:
                score = len(set(target) & set(name_lower))
                if score > best_score:
                    best_score = score
                    best_match = name

        return best_match


@dataclass
class ToolNotFoundError(HangarError):
    """Tool not found in provider's catalog.

    The specified tool doesn't exist on this provider.
    """

    tool_name: str = ""
    """The tool name that wasn't found."""

    available_tools: List[str] = field(default_factory=list)
    """List of available tool names."""

    def __post_init__(self):
        if not self.recovery_hints:
            hints = [
                f"Use registry_tools('{self.provider}') to see available tools",
            ]
            if self.available_tools:
                similar = self._find_similar()
                if similar:
                    hints.append(f"Did you mean: {similar}?")
            self.recovery_hints = hints
        super().__post_init__()

    def _find_similar(self) -> Optional[str]:
        """Find similar tool name for 'did you mean' suggestion."""
        if not self.tool_name or not self.available_tools:
            return None

        target = self.tool_name.lower()
        for name in self.available_tools:
            if target in name.lower() or name.lower() in target:
                return name
        return None


@dataclass
class TimeoutError(HangarError):
    """Operation timed out.

    The operation took longer than the specified timeout.
    """

    timeout_seconds: float = 0.0
    """The timeout that was exceeded."""

    elapsed_seconds: float = 0.0
    """How long the operation ran before timing out."""

    def __post_init__(self):
        if not self.recovery_hints:
            self.recovery_hints = [
                f"Increase timeout: timeout={int(self.timeout_seconds * 2)}",
                "Check if provider is overloaded",
                f"Check provider health: registry_details('{self.provider}')",
            ]
        super().__post_init__()


@dataclass
class RateLimitError(HangarError):
    """Rate limit exceeded.

    Too many requests in the given time window.
    """

    limit: int = 0
    """The rate limit that was exceeded."""

    window_seconds: int = 0
    """The time window for the rate limit."""

    retry_after_seconds: float = 0.0
    """Suggested wait time before retrying."""

    def __post_init__(self):
        if not self.recovery_hints:
            self.recovery_hints = [
                f"Wait {self.retry_after_seconds:.1f}s before retrying",
                "Reduce request frequency",
                "Configure higher rate limits in config.yaml",
            ]
        super().__post_init__()


@dataclass
class ProviderDegradedError(HangarError):
    """Provider is in degraded state.

    The provider has experienced multiple failures and is
    in a backoff period.
    """

    consecutive_failures: int = 0
    """Number of consecutive failures."""

    backoff_remaining_s: float = 0.0
    """Time remaining in backoff period."""

    def __post_init__(self):
        if not self.recovery_hints:
            self.recovery_hints = [
                f"Wait {self.backoff_remaining_s:.1f}s for automatic recovery",
                f"Provider had {self.consecutive_failures} consecutive failures",
                "Check provider logs for root cause",
                "Use registry_start() to force restart",
            ]
        super().__post_init__()


# =============================================================================
# Error Mapping Utilities
# =============================================================================


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive)."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def _create_json_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for JSON parsing failures."""
    exc_str = str(exc)
    preview = exc_str[:100] if len(exc_str) > 100 else exc_str
    return ProviderProtocolError(
        message=f"{provider or 'Provider'} returned invalid response",
        provider=provider,
        operation=operation,
        technical_details=f"JSON parse error: {exc_str}",
        raw_response=preview,
        original_exception=exc,
        context=context,
    )


def _create_timeout_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for timeout failures."""
    timeout = context.get("timeout", 30.0)
    return TimeoutError(
        message=f"Operation timed out after {timeout}s",
        provider=provider,
        operation=operation,
        technical_details=str(exc),
        timeout_seconds=timeout,
        original_exception=exc,
        context=context,
    )


def _create_network_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for network failures."""
    return NetworkError(
        message=f"Unable to reach {provider or 'provider'}",
        provider=provider,
        operation=operation,
        technical_details=str(exc),
        original_exception=exc,
        context=context,
    )


def _create_crash_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for process crashes."""
    exit_code = context.get("exit_code")
    signal_name = None
    if exit_code and exit_code < 0:
        import signal as sig

        try:
            signal_name = sig.Signals(-exit_code).name
        except (ValueError, AttributeError):
            pass

    return ProviderCrashError(
        message=f"{provider or 'Provider'} terminated unexpectedly",
        provider=provider,
        operation=operation,
        technical_details=str(exc),
        exit_code=exit_code,
        signal_name=signal_name,
        original_exception=exc,
        context=context,
    )


def _create_rate_limit_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for rate limit failures."""
    return RateLimitError(
        message="Too many requests",
        provider=provider,
        operation=operation,
        technical_details=str(exc),
        original_exception=exc,
        context=context,
    )


def _create_provider_not_found_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for provider not found."""
    return ProviderNotFoundError(
        message=f"Provider '{provider}' not found",
        provider=provider,
        operation=operation,
        technical_details=str(exc),
        original_exception=exc,
        context=context,
    )


def _create_tool_not_found_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for tool not found."""
    tool_name = context.get("tool_name", "")
    return ToolNotFoundError(
        message=f"Tool '{tool_name}' not found on provider '{provider}'",
        provider=provider,
        operation=operation,
        tool_name=tool_name,
        technical_details=str(exc),
        original_exception=exc,
        context=context,
    )


def _create_client_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create error for client communication failures."""
    exc_str = str(exc)
    if _matches_keywords(exc_str, ["malformed", "json"]):
        return TransientError(
            message=f"Communication error with {provider or 'provider'}",
            provider=provider,
            operation=operation,
            technical_details=exc_str,
            recovery_hints=[
                "This is usually a transient error",
                "Retry the operation",
                f"Check provider status: registry_details('{provider}')",
            ],
            original_exception=exc,
            context=context,
        )

    return HangarError(
        message=f"Client error: {exc_str}",
        provider=provider,
        operation=operation,
        technical_details=exc_str,
        recovery_hints=[
            "Check provider status",
            "Restart the provider if needed",
        ],
        original_exception=exc,
        context=context,
    )


def _create_generic_error(exc: Exception, provider: str, operation: str, context: dict) -> HangarError:
    """Create generic error as fallback."""
    exc_str = str(exc)
    exc_type = type(exc).__name__
    return HangarError(
        message=f"Operation failed: {exc_str}",
        provider=provider,
        operation=operation,
        technical_details=f"{exc_type}: {exc_str}",
        recovery_hints=[
            "Check the logs for more details",
            f"Provider status: registry_details('{provider}')" if provider else "Check provider configuration",
        ],
        original_exception=exc,
        context=context,
    )


# Error detection rules: (keywords to match, detector function, creator function)
_ERROR_MATCHERS: list[tuple[list[str], Callable, Callable]] = [
    # JSON errors - check type name and message
    (
        ["json"],
        lambda exc_type, exc_str: "JSONDecodeError" in exc_type or "json" in exc_str.lower(),
        _create_json_error,
    ),
    # Timeout errors
    (
        ["timeout"],
        lambda exc_type, exc_str: "timeout" in exc_str.lower() or "TimeoutError" in exc_type,
        _create_timeout_error,
    ),
    # Network errors
    (
        ["connection", "network", "dns", "eai_again", "econnrefused"],
        lambda exc_type, exc_str: _matches_keywords(
            exc_str, ["connection", "network", "dns", "eai_again", "econnrefused"]
        ),
        _create_network_error,
    ),
    # Crash errors
    (
        ["exit code", "sigkill", "terminated", "process died"],
        lambda exc_type, exc_str: _matches_keywords(exc_str, ["exit code", "sigkill", "terminated", "process died"]),
        _create_crash_error,
    ),
    # Rate limit
    (["rate limit"], lambda exc_type, exc_str: "rate limit" in exc_str.lower(), _create_rate_limit_error),
    # Provider not found
    (
        ["not found", "provider"],
        lambda exc_type, exc_str: "not found" in exc_str.lower() and "provider" in exc_str.lower(),
        _create_provider_not_found_error,
    ),
    # Tool not found
    (
        ["not found", "tool"],
        lambda exc_type, exc_str: "not found" in exc_str.lower() and "tool" in exc_str.lower(),
        _create_tool_not_found_error,
    ),
    # Client errors
    (
        ["client"],
        lambda exc_type, exc_str: "client" in exc_str.lower() or "ClientError" in exc_type,
        _create_client_error,
    ),
]


def map_exception_to_hangar_error(
    exc: Exception,
    provider: str = "",
    operation: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> HangarError:
    """Map a low-level exception to a rich HangarError.

    This function converts Python exceptions and domain exceptions
    into user-friendly HangarError instances with appropriate
    recovery hints.

    Args:
        exc: The original exception.
        provider: Provider ID if known.
        operation: Operation being performed.
        context: Additional context data.

    Returns:
        A HangarError subclass appropriate for the exception type.
    """
    context = context or {}

    # Already a HangarError - return as-is
    if isinstance(exc, HangarError):
        return exc

    exc_str = str(exc)
    exc_type = type(exc).__name__

    # Try each matcher in order
    for _, detector, creator in _ERROR_MATCHERS:
        if detector(exc_type, exc_str):
            return creator(exc, provider, operation, context)

    # Default: wrap as generic HangarError
    return _create_generic_error(exc, provider, operation, context)


def is_retryable(error: Exception) -> bool:
    """Check if an error is retryable.

    Args:
        error: The error to check

    Returns:
        True if the error is transient and can be retried
    """
    if isinstance(error, TransientError):
        return error.retryable

    if isinstance(error, HangarError):
        # Specific types that are retryable
        if isinstance(error, (ProviderProtocolError, NetworkError, TimeoutError)):
            return True
        return False

    # For non-HangarError, check common patterns
    exc_str = str(error).lower()
    exc_type = type(error).__name__.lower()

    retryable_patterns = [
        "timeout",
        "timed out",
        "connection",
        "json",
        "malformed",
        "temporary",
        "transient",
        "retry",
        "network",
    ]

    for pattern in retryable_patterns:
        if pattern in exc_str or pattern in exc_type:
            return True

    return False


class ErrorClassifier:
    """Classifies errors as transient or permanent and provides recovery hints.

    This classifier analyzes error messages and types to determine:
    - Whether the error is transient (retry may help) or permanent (retry won't help)
    - Specific error reason for debugging
    - Actionable recovery hints for users
    """

    # Transient errors - these should be retried
    TRANSIENT_PATTERNS = {
        "timeout": "Retry with longer timeout or wait for provider recovery",
        "timed out": "Retry with longer timeout or wait for provider recovery",
        "connection_refused": "Provider may be starting, retry in 1-2 seconds",
        "connection refused": "Provider may be starting, retry in 1-2 seconds",
        "service_unavailable": "Provider overloaded, implement backoff",
        "service unavailable": "Provider overloaded, implement backoff",
        "network_error": "Check network connectivity, retry",
        "network error": "Check network connectivity, retry",
        "econnrefused": "Provider may be starting, retry in 1-2 seconds",
        "temporary": "Retry the operation",
        "transient": "Retry the operation",
        "json": "Provider returned malformed response, retry",
        "malformed": "Provider returned malformed response, retry",
        "connection reset": "Network issue, retry",
        "broken pipe": "Network issue, retry",
    }

    # Permanent errors - these should NOT be retried
    PERMANENT_PATTERNS = {
        "division by zero": ("validation_error", ["Check arguments: divisor cannot be zero"]),
        "zerodivision": ("validation_error", ["Check arguments: divisor cannot be zero"]),
        "invalid_argument": ("validation_error", ["Review tool schema and fix arguments"]),
        "invalid argument": ("validation_error", ["Review tool schema and fix arguments"]),
        "tool_not_found": ("configuration_error", ["Verify tool name exists on provider"]),
        "tool not found": ("configuration_error", ["Verify tool name exists on provider"]),
        "provider_not_found": ("configuration_error", ["Check provider ID in registry_status()"]),
        "provider not found": ("configuration_error", ["Check provider ID in registry_status()"]),
        "permission_denied": ("authorization_error", ["Verify permissions for requested resource"]),
        "permission denied": ("authorization_error", ["Verify permissions for requested resource"]),
        "access denied": ("authorization_error", ["Path outside allowed directories or resource access not permitted"]),
        "unauthorized": ("authorization_error", ["Check authentication credentials"]),
        "forbidden": ("authorization_error", ["Check authorization permissions"]),
        "not found": ("not_found_error", ["Verify the resource or tool exists"]),
        "value error": ("validation_error", ["Check input arguments format and values"]),
        "valueerror": ("validation_error", ["Check input arguments format and values"]),
        "type error": ("validation_error", ["Check input arguments types"]),
        "typeerror": ("validation_error", ["Check input arguments types"]),
        "file not found": ("resource_error", ["Verify the file path exists"]),
        "no such file": ("resource_error", ["Verify the file path exists"]),
        "syntax error": ("validation_error", ["Check input syntax (e.g., SQL, JSON)"]),
        "invalid syntax": ("validation_error", ["Check input syntax"]),
    }

    @classmethod
    def classify(cls, error: Exception) -> Dict[str, Any]:
        """Classify an error and return metadata.

        Args:
            error: The exception to classify

        Returns:
            Dictionary with:
            - is_transient: bool - whether retry may help
            - final_error_reason: str - classification like "permanent: validation_error"
            - recovery_hints: List[str] - actionable steps
            - should_retry: bool - recommendation
        """
        error_message = str(error).lower()
        error_type = type(error).__name__

        # Check if it's already a HangarError with hints
        if isinstance(error, HangarError):
            is_transient = isinstance(error, (TransientError, ProviderProtocolError, NetworkError, TimeoutError))
            return {
                "is_transient": is_transient,
                "final_error_reason": f"{'transient' if is_transient else 'permanent'}: {error_type}",
                "recovery_hints": error.recovery_hints if error.recovery_hints else cls._default_hints(is_transient),
                "should_retry": is_transient,
            }

        # Check permanent patterns first (more specific)
        for pattern, (reason, hints) in cls.PERMANENT_PATTERNS.items():
            if pattern in error_message or pattern in error_type.lower():
                return {
                    "is_transient": False,
                    "final_error_reason": f"permanent: {reason}",
                    "recovery_hints": hints,
                    "should_retry": False,
                }

        # Check transient patterns
        for pattern, hint in cls.TRANSIENT_PATTERNS.items():
            if pattern in error_message or pattern in error_type.lower():
                return {
                    "is_transient": True,
                    "final_error_reason": f"transient: {pattern.replace('_', ' ')}",
                    "recovery_hints": [hint],
                    "should_retry": True,
                }

        # Use is_retryable() for additional checks
        if is_retryable(error):
            return {
                "is_transient": True,
                "final_error_reason": "transient: unclassified",
                "recovery_hints": ["Retry the operation", "Check provider status"],
                "should_retry": True,
            }

        # Unknown = conservative approach (assume may be transient for safety)
        return {
            "is_transient": True,
            "final_error_reason": "unknown: unclassified error",
            "recovery_hints": [
                "Check the error message",
                "Retry may help",
                "Contact support if persistent",
            ],
            "should_retry": True,
        }

    @classmethod
    def _default_hints(cls, is_transient: bool) -> List[str]:
        """Get default hints based on error type."""
        if is_transient:
            return ["Retry the operation", "Check provider logs for details"]
        return ["Review the error message", "Check input arguments", "Verify provider configuration"]
