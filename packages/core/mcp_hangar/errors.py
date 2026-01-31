"""Human-readable error classes with recovery hints.

This module provides rich error types that include:

- Clear user-facing messages
- Technical details for debugging
- Actionable recovery hints
- Related log references

Error output example for tool invocation::

    RichToolInvocationError: Provider 'sqlite' did not respond in time
      Provider: sqlite
      Tool: query
      Operation: invoke

      Possible causes:
        - The operation is taking longer than expected
        - The provider is stuck or deadlocked
        - Resource constraints (CPU/memory)

      Technical details:
        Timeout: 30.0s, elapsed: 30.50s

      What you can try:
        1. Retry with longer timeout: timeout=60
        2. Check provider status: registry_details('sqlite')
        3. Retry the operation (may be transient)

      Correlation ID: abc-123-def

Usage::

    from mcp_hangar.errors import create_timeout_tool_error

    error = create_timeout_tool_error(
        provider="sqlite",
        tool="query",
        timeout_s=30.0,
        elapsed_s=30.5,
        correlation_id="abc-123",
    )
    raise error

Factory functions for common error types:
- create_timeout_tool_error() - Provider did not respond in time
- create_crash_tool_error() - Provider crashed (with signal detection)
- create_argument_tool_error() - Invalid arguments (with schema hints)
- create_provider_error() - Generic provider error

See docs/guides/UX_IMPROVEMENTS.md for more examples.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Kategoria bledu dla lepszej diagnostyki.

    Attributes:
        USER_ERROR: Blad po stronie uzytkownika (zle argumenty, zla nazwa narzedzia)
        PROVIDER_ERROR: Blad po stronie providera (crash, blad logiki)
        INFRA_ERROR: Blad infrastruktury (timeout, siec, brak zasobow)
    """

    USER_ERROR = "user_error"
    PROVIDER_ERROR = "provider_error"
    INFRA_ERROR = "infra_error"


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

    recovery_hints: list[str] = field(default_factory=list)
    """Actionable steps the user can take to resolve the issue."""

    # Technical context
    provider: str = ""
    """The provider that caused the error."""

    operation: str = ""
    """The operation that was being performed."""

    technical_details: str = ""
    """Technical error details for debugging."""

    # Debugging
    related_logs: str | None = None
    """Path to relevant log entries (e.g., '/logs/mcp-hangar.log:580')."""

    issue_url: str | None = None
    """Link to known issue or documentation."""

    original_exception: Exception | None = None
    """The original exception that caused this error."""

    context: dict[str, Any] = field(default_factory=dict)
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

    def to_dict(self) -> dict[str, Any]:
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

    raw_response: str | None = None
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

    exit_code: int | None = None
    """Process exit code if available."""

    signal_name: str | None = None
    """Signal name if killed by signal (e.g., SIGKILL)."""

    idle_duration_s: float | None = None
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

    hostname: str | None = None
    """The hostname that was unreachable."""

    error_code: str | None = None
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

    config_path: str | None = None
    """Path to the configuration file."""

    field_name: str | None = None
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
class RichProviderNotFoundError(HangarError):
    """Provider not found in registry (rich UX version).

    The specified provider ID doesn't exist in the configuration.
    Use this for user-facing error messages with recovery hints.

    For domain logic, use mcp_hangar.domain.exceptions.ProviderNotFoundError.
    """

    available_providers: list[str] = field(default_factory=list)
    """List of available provider IDs."""

    def __post_init__(self):
        if not self.recovery_hints:
            hints = [
                "Use hangar_list() to see available providers",
            ]
            if self.available_providers:
                similar = self._find_similar()
                if similar:
                    hints.append(f"Did you mean: {similar}?")
            hints.append("Add provider to config.yaml")
            self.recovery_hints = hints
        super().__post_init__()

    def _find_similar(self) -> str | None:
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


# Backward compatibility alias
ProviderNotFoundError = RichProviderNotFoundError


@dataclass
class RichToolNotFoundError(HangarError):
    """Tool not found in provider's catalog (rich UX version).

    The specified tool doesn't exist on this provider.
    Use this for user-facing error messages with recovery hints.

    For domain logic, use mcp_hangar.domain.exceptions.ToolNotFoundError.
    """

    tool_name: str = ""
    """The tool name that wasn't found."""

    available_tools: list[str] = field(default_factory=list)
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

    def _find_similar(self) -> str | None:
        """Find similar tool name for 'did you mean' suggestion."""
        if not self.tool_name or not self.available_tools:
            return None

        target = self.tool_name.lower()
        for name in self.available_tools:
            if target in name.lower() or name.lower() in target:
                return name
        return None


# Backward compatibility alias
ToolNotFoundError = RichToolNotFoundError


@dataclass
class RichToolInvocationError(HangarError):
    """Wzbogacony blad wywolania narzedzia z kontekstem diagnostycznym.

    Zawiera:
    - Klasyfikacje bledu (user/provider/infra)
    - Szczegoly techniczne (timeout, exit_code, stderr)
    - Kontekstowe kroki naprawcze
    - Informacje o mozliwosci retry

    Example output::

        RichToolInvocationError: Provider 'sqlite' did not respond in time
          Provider: sqlite
          Tool: query
          Operation: invoke

          Possible causes:
            - The operation is taking longer than expected
            - The provider is stuck or deadlocked

          Technical details:
            Timeout: 30.0s, elapsed: 30.50s

          What you can try:
            1. Retry with longer timeout: timeout=60
            2. Check provider status: registry_details('sqlite')

          Correlation ID: abc-123-def
    """

    # Kontekst wywolania
    tool_name: str = ""
    """Name of the tool that was invoked."""

    arguments: dict[str, Any] = field(default_factory=dict)
    """Arguments passed to the tool."""

    correlation_id: str = ""
    """Correlation ID for tracing."""

    # Szczegoly bledu
    category: ErrorCategory = ErrorCategory.INFRA_ERROR
    """Error category for classification."""

    timeout_s: float | None = None
    """Timeout value in seconds."""

    elapsed_s: float | None = None
    """Elapsed time before failure."""

    exit_code: int | None = None
    """Process exit code if applicable."""

    signal_name: str | None = None
    """Signal name if killed by signal (e.g., SIGKILL)."""

    stderr_preview: str | None = None
    """Preview of stderr output from provider."""

    # Schema dla bledow argumentow
    expected_schema: dict[str, Any] | None = None
    """Expected tool schema for argument errors."""

    schema_hint: str | None = None
    """Hint about schema mismatch (e.g., 'Did you mean sql instead of query?')."""

    # Retry info
    is_retryable: bool = True
    """Whether the operation can be retried."""

    retry_after_s: float | None = None
    """Suggested delay before retry."""

    # Mozliwe przyczyny
    possible_causes: list[str] = field(default_factory=list)
    """List of possible causes for the error."""

    def __post_init__(self):
        # Generuj recovery_hints na podstawie kategorii jesli nie podano
        if not self.recovery_hints:
            self.recovery_hints = self._generate_recovery_hints()
        super().__post_init__()

    def _generate_recovery_hints(self) -> list[str]:
        """Generuj kontekstowe hinty naprawcze."""
        hints = []

        if self.category == ErrorCategory.USER_ERROR:
            if self.expected_schema:
                hints.append(f"Check tool schema: registry_tools('{self.provider}')")
            if self.schema_hint:
                hints.append(self.schema_hint)
            hints.append("Verify argument names and types")

        elif self.category == ErrorCategory.PROVIDER_ERROR:
            hints.append("Provider will auto-restart on next use")
            hints.append(f"Check provider logs: registry_details('{self.provider}')")
            if self.exit_code == 137:  # SIGKILL/OOM
                hints.append("Consider increasing memory limit in config")
            if self.stderr_preview:
                hints.append("Review stderr output below for details")

        elif self.category == ErrorCategory.INFRA_ERROR:
            if self.timeout_s:
                new_timeout = int(self.timeout_s * 2)
                hints.append(f"Retry with longer timeout: timeout={new_timeout}")
            hints.append(f"Check provider status: registry_details('{self.provider}')")
            if self.is_retryable:
                hints.append("Retry the operation (may be transient)")

        return hints

    def __str__(self) -> str:
        """Formatuj blad z pelnym kontekstem."""
        lines = [f"\n{self.__class__.__name__}: {self.message}"]

        # Kontekst
        if self.provider:
            lines.append(f"  Provider: {self.provider}")
        if self.tool_name:
            lines.append(f"  Tool: {self.tool_name}")
        if self.operation:
            lines.append(f"  Operation: {self.operation}")

        # Mozliwe przyczyny
        if self.possible_causes:
            lines.append("")
            lines.append("  Possible causes:")
            for cause in self.possible_causes:
                lines.append(f"    - {cause}")

        # Szczegoly techniczne
        if self.technical_details or self.timeout_s or self.exit_code is not None:
            lines.append("")
            lines.append("  Technical details:")
            if self.timeout_s and self.elapsed_s:
                lines.append(f"    Timeout: {self.timeout_s}s, elapsed: {self.elapsed_s:.2f}s")
            if self.exit_code is not None:
                exit_info = f"Exit code: {self.exit_code}"
                if self.signal_name:
                    exit_info += f" ({self.signal_name})"
                lines.append(f"    {exit_info}")
            if self.technical_details:
                lines.append(f"    {self.technical_details}")

        # Stderr preview
        if self.stderr_preview:
            lines.append("")
            lines.append("  Provider stderr:")
            for stderr_line in self.stderr_preview.split("\n")[:5]:
                lines.append(f"    | {stderr_line}")

        # Recovery hints
        if self.recovery_hints:
            lines.append("")
            lines.append("  What you can try:")
            for i, hint in enumerate(self.recovery_hints, 1):
                lines.append(f"    {i}. {hint}")

        # Correlation ID
        if self.correlation_id:
            lines.append("")
            lines.append(f"  Correlation ID: {self.correlation_id}")

        return "\n".join(lines)


# =============================================================================
# Factory Functions for RichToolInvocationError
# =============================================================================


def create_timeout_tool_error(
    provider: str,
    tool: str,
    timeout_s: float,
    elapsed_s: float,
    correlation_id: str = "",
    arguments: dict[str, Any] | None = None,
) -> RichToolInvocationError:
    """Create a timeout error with full context.

    Args:
        provider: Provider ID.
        tool: Tool name.
        timeout_s: Configured timeout in seconds.
        elapsed_s: Actual elapsed time.
        correlation_id: Optional correlation ID.
        arguments: Optional tool arguments.

    Returns:
        RichToolInvocationError configured for timeout scenario.
    """
    return RichToolInvocationError(
        message=f"Provider '{provider}' did not respond in time",
        provider=provider,
        tool_name=tool,
        operation="invoke",
        category=ErrorCategory.INFRA_ERROR,
        timeout_s=timeout_s,
        elapsed_s=elapsed_s,
        correlation_id=correlation_id,
        arguments=arguments or {},
        is_retryable=True,
        possible_causes=[
            "The operation is taking longer than expected",
            "The provider is stuck or deadlocked",
            "Resource constraints (CPU/memory)",
        ],
    )


def create_crash_tool_error(
    provider: str,
    tool: str,
    exit_code: int | None,
    stderr_preview: str | None = None,
    correlation_id: str = "",
    elapsed_s: float | None = None,
) -> RichToolInvocationError:
    """Create a crash error with full context.

    Args:
        provider: Provider ID.
        tool: Tool name.
        exit_code: Process exit code.
        stderr_preview: Preview of stderr output.
        correlation_id: Optional correlation ID.
        elapsed_s: Time elapsed before crash.

    Returns:
        RichToolInvocationError configured for crash scenario.
    """
    import signal as sig

    signal_name = None
    if exit_code is not None and exit_code < 0:
        try:
            signal_name = sig.Signals(-exit_code).name
        except (ValueError, AttributeError):
            pass
    elif exit_code is not None and exit_code > 128:
        try:
            signal_name = sig.Signals(exit_code - 128).name
        except (ValueError, AttributeError):
            pass

    causes = ["Internal provider error"]
    if exit_code == 137 or signal_name == "SIGKILL":
        causes = [
            "Out of memory (OOM killed by system)",
            "Container resource limits exceeded",
            "Manual kill signal",
        ]
    elif exit_code == 139 or signal_name == "SIGSEGV":
        causes = [
            "Segmentation fault in provider",
            "Memory corruption",
        ]

    return RichToolInvocationError(
        message=f"Provider '{provider}' crashed during execution",
        provider=provider,
        tool_name=tool,
        operation="invoke",
        category=ErrorCategory.PROVIDER_ERROR,
        exit_code=exit_code,
        signal_name=signal_name,
        stderr_preview=stderr_preview,
        correlation_id=correlation_id,
        elapsed_s=elapsed_s,
        is_retryable=True,  # Provider will auto-restart
        possible_causes=causes,
    )


def create_argument_tool_error(
    provider: str,
    tool: str,
    provided_args: dict[str, Any],
    expected_schema: dict[str, Any] | None = None,
    hint: str | None = None,
    correlation_id: str = "",
) -> RichToolInvocationError:
    """Create an argument error with full context.

    Args:
        provider: Provider ID.
        tool: Tool name.
        provided_args: Arguments that were provided.
        expected_schema: Expected tool schema.
        hint: Hint about what's wrong.
        correlation_id: Optional correlation ID.

    Returns:
        RichToolInvocationError configured for argument error scenario.
    """
    return RichToolInvocationError(
        message=f"Invalid arguments for tool '{tool}'",
        provider=provider,
        tool_name=tool,
        operation="invoke",
        category=ErrorCategory.USER_ERROR,
        arguments=provided_args,
        expected_schema=expected_schema,
        schema_hint=hint,
        correlation_id=correlation_id,
        is_retryable=False,  # User must fix arguments
        possible_causes=[
            "Missing required argument",
            "Wrong argument name",
            "Invalid argument type",
        ],
    )


def create_provider_error(
    provider: str,
    tool: str,
    error_message: str,
    stderr_preview: str | None = None,
    correlation_id: str = "",
    is_retryable: bool = True,
) -> RichToolInvocationError:
    """Create a generic provider error with full context.

    Args:
        provider: Provider ID.
        tool: Tool name.
        error_message: Error message from provider.
        stderr_preview: Preview of stderr output.
        correlation_id: Optional correlation ID.
        is_retryable: Whether the error is retryable.

    Returns:
        RichToolInvocationError configured for generic provider error.
    """
    return RichToolInvocationError(
        message=f"Provider '{provider}' returned an error: {error_message}",
        provider=provider,
        tool_name=tool,
        operation="invoke",
        category=ErrorCategory.PROVIDER_ERROR,
        technical_details=error_message,
        stderr_preview=stderr_preview,
        correlation_id=correlation_id,
        is_retryable=is_retryable,
        possible_causes=[
            "Tool execution failed",
            "Invalid input for tool logic",
            "Provider internal error",
        ],
    )


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
    return RichProviderNotFoundError(
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
    return RichToolNotFoundError(
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
    context: dict[str, Any] | None = None,
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
        if isinstance(error, ProviderProtocolError | NetworkError | TimeoutError):
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
    def classify(cls, error: Exception) -> dict[str, Any]:
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
            is_transient = isinstance(error, TransientError | ProviderProtocolError | NetworkError | TimeoutError)
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
    def _default_hints(cls, is_transient: bool) -> list[str]:
        """Get default hints based on error type."""
        if is_transient:
            return ["Retry the operation", "Check provider logs for details"]
        return ["Review the error message", "Check input arguments", "Verify provider configuration"]
