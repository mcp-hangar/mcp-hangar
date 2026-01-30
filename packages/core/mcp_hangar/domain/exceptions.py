"""
Domain exceptions for MCP Hangar.

All domain-specific exceptions should be defined here.
These exceptions carry context and can be serialized to structured error responses.
"""

from typing import Any


class MCPError(Exception):
    """Base exception for all MCP registry errors.

    Provides structured error information with context for debugging and logging.
    """

    def __init__(
        self,
        message: str,
        provider_id: str = "",
        operation: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider_id = provider_id
        self.operation = operation
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to structured error dictionary for API responses."""
        return {
            "error": self.message,
            "provider_id": self.provider_id,
            "operation": self.operation,
            "details": self.details,
            "type": self.__class__.__name__,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"provider_id={self.provider_id!r}, "
            f"operation={self.operation!r})"
        )


# --- Provider Lifecycle Exceptions ---


class ProviderError(MCPError):
    """Base exception for provider-related errors."""

    pass


class ProviderNotFoundError(ProviderError):
    """Raised when a provider is not found in the registry."""

    def __init__(self, provider_id: str):
        super().__init__(
            message=f"Provider not found: {provider_id}",
            provider_id=provider_id,
            operation="lookup",
        )


class ProviderStartError(ProviderError):
    """Raised when a provider fails to start.

    Contains detailed diagnostics to help users understand and fix the issue:
    - reason: High-level reason for failure
    - stderr: Captured stderr output from the process (if available)
    - exit_code: Process exit code (if available)
    - suggestion: Actionable suggestion for fixing the issue
    """

    def __init__(
        self,
        provider_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
        suggestion: str | None = None,
    ):
        # Build user-friendly message
        message = f"Failed to start provider: {reason}"
        if suggestion:
            message = f"{message}. Suggestion: {suggestion}"

        super().__init__(
            message=message,
            provider_id=provider_id,
            operation="start",
            details=details or {},
        )
        self.reason = reason
        self.stderr = stderr
        self.exit_code = exit_code
        self.suggestion = suggestion

        # Add diagnostics to details for structured logging/API responses
        if stderr:
            self.details["stderr"] = stderr[:2000] if len(stderr) > 2000 else stderr
        if exit_code is not None:
            self.details["exit_code"] = exit_code
        if suggestion:
            self.details["suggestion"] = suggestion

    def get_user_message(self) -> str:
        """Get a user-friendly error message with all available context."""
        lines = [f"Failed to start provider '{self.provider_id}': {self.reason}"]

        if self.exit_code is not None:
            lines.append(f"  Exit code: {self.exit_code}")

        if self.stderr:
            # Show first few lines of stderr
            stderr_lines = self.stderr.strip().split("\n")[:5]
            if stderr_lines:
                lines.append("  Process output:")
                for line in stderr_lines:
                    lines.append(f"    {line}")
                if len(self.stderr.strip().split("\n")) > 5:
                    lines.append("    ... (truncated)")

        if self.suggestion:
            lines.append(f"  Suggestion: {self.suggestion}")

        return "\n".join(lines)


class ProviderDegradedError(ProviderError):
    """Raised when a provider is in degraded state and cannot accept requests."""

    def __init__(
        self,
        provider_id: str,
        backoff_remaining: float = 0,
        consecutive_failures: int = 0,
    ):
        super().__init__(
            message=f"Provider is degraded, retry in {backoff_remaining:.1f}s",
            provider_id=provider_id,
            operation="ensure_ready",
            details={
                "backoff_remaining_s": backoff_remaining,
                "consecutive_failures": consecutive_failures,
            },
        )
        self.backoff_remaining = backoff_remaining
        self.consecutive_failures = consecutive_failures


class CannotStartProviderError(ProviderError):
    """Raised when provider cannot be started due to backoff or other constraints."""

    def __init__(self, provider_id: str, reason: str, time_until_retry: float = 0):
        super().__init__(
            message=f"Cannot start provider: {reason}",
            provider_id=provider_id,
            operation="start",
            details={"time_until_retry_s": time_until_retry},
        )
        self.reason = reason
        self.time_until_retry = time_until_retry


class ProviderNotReadyError(ProviderError):
    """Raised when an operation requires READY state but provider is not ready."""

    def __init__(self, provider_id: str, current_state: str):
        super().__init__(
            message=f"Provider is not ready (state={current_state})",
            provider_id=provider_id,
            operation="invoke",
            details={"current_state": current_state},
        )
        self.current_state = current_state


class InvalidStateTransitionError(ProviderError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, provider_id: str, from_state: str, to_state: str):
        super().__init__(
            message=f"Invalid state transition: {from_state} -> {to_state}",
            provider_id=provider_id,
            operation="transition",
            details={"from_state": from_state, "to_state": to_state},
        )
        self.from_state = from_state
        self.to_state = to_state


# --- Tool Invocation Exceptions ---


class ToolError(MCPError):
    """Base exception for tool-related errors."""

    pass


class ToolNotFoundError(ToolError):
    """Raised when a tool is not found in the provider's catalog."""

    def __init__(self, provider_id: str, tool_name: str):
        super().__init__(
            message=f"Tool not found: {tool_name}",
            provider_id=provider_id,
            operation="invoke",
            details={"tool_name": tool_name},
        )
        self.tool_name = tool_name


class ToolInvocationError(ToolError):
    """Raised when a tool invocation fails."""

    def __init__(self, provider_id: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            provider_id=provider_id,
            operation="invoke",
            details=details or {},
        )


class ToolTimeoutError(ToolError):
    """Raised when a tool invocation times out."""

    def __init__(self, provider_id: str, tool_name: str, timeout: float):
        super().__init__(
            message=f"Tool invocation timed out after {timeout}s",
            provider_id=provider_id,
            operation="invoke",
            details={"tool_name": tool_name, "timeout_s": timeout},
        )
        self.tool_name = tool_name
        self.timeout = timeout


# --- Client/Communication Exceptions ---


class ClientError(MCPError):
    """Raised when the stdio client encounters an error."""

    def __init__(
        self,
        message: str,
        provider_id: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            provider_id=provider_id,
            operation="client",
            details=details or {},
        )


class ClientNotConnectedError(ClientError):
    """Raised when attempting to use a client that is not connected."""

    def __init__(self, provider_id: str = ""):
        super().__init__(message="Client is not connected", provider_id=provider_id)


class ClientTimeoutError(ClientError):
    """Raised when a client operation times out."""

    def __init__(self, provider_id: str = "", timeout: float = 0, operation: str = "call"):
        super().__init__(
            message=f"Client operation timed out after {timeout}s",
            provider_id=provider_id,
            details={"timeout_s": timeout, "operation": operation},
        )
        self.timeout = timeout


# --- Validation Exceptions ---


class ValidationError(MCPError):
    """Raised when input validation fails."""

    def __init__(
        self,
        message: str,
        field: str = "",
        value: Any = None,
        details: dict[str, Any] | None = None,
    ):
        base_details = {"field": field}
        if value is not None:
            # Sanitize value for logging (truncate if too long)
            str_value = str(value)
            if len(str_value) > 100:
                str_value = str_value[:100] + "..."
            base_details["value"] = str_value
        if details:
            base_details.update(details)

        super().__init__(message=message, operation="validation", details=base_details)
        self.field = field
        self.value = value


class ConfigurationError(MCPError):
    """Raised when configuration is invalid."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message=message, operation="configuration", details=details or {})


# --- Rate Limiting Exceptions ---


class RateLimitExceeded(MCPError):
    """Raised when rate limit is exceeded."""

    def __init__(self, provider_id: str = "", limit: int = 0, window_seconds: int = 0):
        super().__init__(
            message=f"Rate limit exceeded: {limit} requests per {window_seconds}s",
            provider_id=provider_id,
            operation="rate_limit",
            details={"limit": limit, "window_seconds": window_seconds},
        )
        self.limit = limit
        self.window_seconds = window_seconds


# --- Authentication Exceptions ---


class AuthenticationError(MCPError):
    """Base class for authentication errors.

    All authentication-related failures inherit from this class,
    enabling unified handling of auth errors.
    """

    def __init__(
        self,
        message: str,
        auth_method: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            operation="authentication",
            details={"auth_method": auth_method, **(details or {})},
        )
        self.auth_method = auth_method


class InvalidCredentialsError(AuthenticationError):
    """Credentials are invalid or malformed.

    Raised when:
    - API key format is invalid
    - JWT signature verification fails
    - Token is malformed
    - Unknown API key
    """

    def __init__(
        self,
        message: str = "Invalid credentials",
        auth_method: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            auth_method=auth_method,
            details=details,
        )


class ExpiredCredentialsError(AuthenticationError):
    """Credentials have expired.

    Raised when:
    - JWT exp claim is in the past
    - API key has passed its expiration date
    """

    def __init__(
        self,
        message: str = "Credentials have expired",
        auth_method: str = "",
        expired_at: float | None = None,
    ):
        super().__init__(
            message=message,
            auth_method=auth_method,
            details={"expired_at": expired_at} if expired_at else None,
        )
        self.expired_at = expired_at


class RevokedCredentialsError(AuthenticationError):
    """Credentials have been revoked.

    Raised when:
    - API key has been explicitly revoked
    - JWT is on a revocation list
    """

    def __init__(
        self,
        message: str = "Credentials have been revoked",
        auth_method: str = "",
        revoked_at: float | None = None,
    ):
        super().__init__(
            message=message,
            auth_method=auth_method,
            details={"revoked_at": revoked_at} if revoked_at else None,
        )
        self.revoked_at = revoked_at


class MissingCredentialsError(AuthenticationError):
    """No credentials provided when authentication is required.

    Raised when:
    - No Authorization header present
    - No API key header present
    - Authentication is required but allow_anonymous is False
    """

    def __init__(
        self,
        message: str = "No credentials provided",
        expected_methods: list[str] | None = None,
    ):
        super().__init__(
            message=message,
            auth_method="none",
            details={"expected_methods": expected_methods} if expected_methods else None,
        )
        self.expected_methods = expected_methods or []


class RateLimitExceededError(AuthenticationError):
    """Rate limit exceeded for authentication attempts.

    Raised when:
    - Too many failed authentication attempts from an IP
    - IP is temporarily locked out
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float | None = None,
    ):
        super().__init__(
            message=message,
            auth_method="rate_limit",
            details={"retry_after": retry_after} if retry_after else None,
        )
        self.retry_after = retry_after


# --- Authorization Exceptions ---


class AuthorizationError(MCPError):
    """Base class for authorization errors.

    All authorization-related failures inherit from this class.
    """

    def __init__(
        self,
        message: str,
        principal_id: str = "",
        action: str = "",
        resource: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            operation="authorization",
            details={
                "principal_id": principal_id,
                "action": action,
                "resource": resource,
                **(details or {}),
            },
        )
        self.principal_id = principal_id
        self.action = action
        self.resource = resource


class AccessDeniedError(AuthorizationError):
    """Principal does not have permission for the requested action.

    The most common authorization error - principal is authenticated
    but lacks the necessary permissions.
    """

    def __init__(
        self,
        principal_id: str,
        action: str,
        resource: str,
        reason: str = "",
    ):
        message = f"Access denied: {principal_id} cannot {action} on {resource}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(
            message=message,
            principal_id=principal_id,
            action=action,
            resource=resource,
            details={"reason": reason} if reason else None,
        )
        self.reason = reason


class InsufficientScopeError(AuthorizationError):
    """Token does not have required scope.

    Raised when JWT token scopes don't include the required scope
    for the requested operation.
    """

    def __init__(
        self,
        principal_id: str,
        required_scope: str,
        available_scopes: list[str] | None = None,
    ):
        super().__init__(
            message=f"Insufficient scope: required '{required_scope}'",
            principal_id=principal_id,
            action="scope_check",
            resource=required_scope,
            details={"available_scopes": available_scopes} if available_scopes else None,
        )
        self.required_scope = required_scope
        self.available_scopes = available_scopes or []


class TenantAccessDeniedError(AuthorizationError):
    """Principal cannot access resources in the specified tenant.

    Raised when a principal attempts to access resources in a tenant
    they don't belong to.
    """

    def __init__(
        self,
        principal_id: str,
        principal_tenant: str | None,
        resource_tenant: str,
    ):
        super().__init__(
            message=f"Access denied: cannot access tenant '{resource_tenant}'",
            principal_id=principal_id,
            action="tenant_access",
            resource=resource_tenant,
            details={
                "principal_tenant": principal_tenant,
                "resource_tenant": resource_tenant,
            },
        )
        self.principal_tenant = principal_tenant
        self.resource_tenant = resource_tenant


# --- Multi-Tenancy Exceptions ---


class TenantNotFoundError(MCPError):
    """Tenant does not exist."""

    def __init__(self, tenant_id: str):
        super().__init__(
            message=f"Tenant not found: {tenant_id}",
            details={"tenant_id": tenant_id},
        )
        self.tenant_id = tenant_id


class TenantSuspendedError(MCPError):
    """Tenant is suspended and cannot perform operations."""

    def __init__(self, tenant_id: str, reason: str = ""):
        super().__init__(
            message=f"Tenant suspended: {tenant_id}" + (f" - {reason}" if reason else ""),
            details={"tenant_id": tenant_id, "reason": reason},
        )
        self.tenant_id = tenant_id


class QuotaExceededError(MCPError):
    """Resource quota exceeded for tenant."""

    def __init__(
        self,
        tenant_id: str,
        resource_type: str,
        limit: int,
        current: int,
        requested: int = 1,
    ):
        super().__init__(
            message=f"Quota exceeded for {resource_type}: {current}/{limit} (requested {requested})",
            details={
                "tenant_id": tenant_id,
                "resource_type": resource_type,
                "limit": limit,
                "current": current,
                "requested": requested,
            },
        )
        self.tenant_id = tenant_id
        self.resource_type = resource_type
        self.limit = limit
        self.current = current


class NamespaceNotFoundError(MCPError):
    """Namespace does not exist."""

    def __init__(self, namespace_id: str, tenant_id: str = ""):
        super().__init__(
            message=f"Namespace not found: {namespace_id}" + (f" in tenant {tenant_id}" if tenant_id else ""),
            details={"namespace_id": namespace_id, "tenant_id": tenant_id},
        )
        self.namespace_id = namespace_id


class CatalogItemNotFoundError(MCPError):
    """Catalog item does not exist."""

    def __init__(self, item_id: str):
        super().__init__(
            message=f"Catalog item not found: {item_id}",
            details={"item_id": item_id},
        )
        self.item_id = item_id


class CatalogItemNotDeployableError(MCPError):
    """Catalog item cannot be deployed (wrong status or failed security review)."""

    def __init__(self, item_id: str, reason: str):
        super().__init__(
            message=f"Catalog item not deployable: {item_id} - {reason}",
            details={"item_id": item_id, "reason": reason},
        )
        self.item_id = item_id


# --- Registry Exceptions ---


class RegistryError(MCPError):
    """Base exception for registry-related errors."""

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            operation="registry",
            details=details or {},
        )


class RegistryConnectionError(RegistryError):
    """Failed to connect to the registry."""

    def __init__(self, url: str, reason: str):
        super().__init__(
            message=f"Failed to connect to registry: {reason}",
            details={"url": url, "reason": reason},
        )
        self.url = url
        self.reason = reason


class RegistryServerNotFoundError(RegistryError):
    """Server not found in the registry."""

    def __init__(self, server_id: str):
        super().__init__(
            message=f"Server not found in registry: {server_id}",
            details={"server_id": server_id},
        )
        self.server_id = server_id


class RegistryAmbiguousSearchError(RegistryError):
    """Multiple servers match the search query."""

    def __init__(self, query: str, matches: list[str]):
        super().__init__(
            message=f"Ambiguous search '{query}': found {len(matches)} matches",
            details={"query": query, "matches": matches},
        )
        self.query = query
        self.matches = matches


# --- Installation Exceptions ---


class InstallationError(MCPError):
    """Base exception for package installation errors."""

    def __init__(
        self,
        message: str,
        package: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            operation="installation",
            details={"package": package, **(details or {})},
        )
        self.package = package


class RuntimeNotAvailableError(InstallationError):
    """Required runtime (npx, uvx, docker) is not available."""

    def __init__(self, runtime: str, suggestion: str | None = None):
        message = f"Runtime not available: {runtime}"
        if suggestion:
            message = f"{message}. {suggestion}"
        super().__init__(
            message=message,
            details={"runtime": runtime, "suggestion": suggestion},
        )
        self.runtime = runtime
        self.suggestion = suggestion


class PackageInstallationError(InstallationError):
    """Package installation failed."""

    def __init__(
        self,
        package: str,
        reason: str,
        stderr: str | None = None,
        exit_code: int | None = None,
    ):
        details: dict[str, Any] = {"reason": reason}
        if stderr:
            details["stderr"] = stderr[:2000] if len(stderr) > 2000 else stderr
        if exit_code is not None:
            details["exit_code"] = exit_code

        super().__init__(
            message=f"Failed to install package '{package}': {reason}",
            package=package,
            details=details,
        )
        self.reason = reason
        self.stderr = stderr
        self.exit_code = exit_code


class PackageVerificationError(InstallationError):
    """Package verification (SHA256) failed."""

    def __init__(self, package: str, expected_hash: str, actual_hash: str):
        super().__init__(
            message="Package verification failed: hash mismatch",
            package=package,
            details={
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
            },
        )
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash


class MissingSecretsError(MCPError):
    """Required secrets are not available."""

    def __init__(self, provider_name: str, missing: list[str], instructions: str | None = None):
        super().__init__(
            message=f"Missing required secrets for '{provider_name}': {', '.join(missing)}",
            operation="secrets",
            details={
                "provider_name": provider_name,
                "missing": missing,
                "instructions": instructions,
            },
        )
        self.provider_name = provider_name
        self.missing = missing
        self.instructions = instructions


class UnverifiedProviderError(MCPError):
    """Attempted to load an unverified provider without explicit flag."""

    def __init__(self, provider_name: str):
        super().__init__(
            message=f"Provider '{provider_name}' is not verified. Use force_unverified=True to load.",
            operation="load",
            details={"provider_name": provider_name},
        )
        self.provider_name = provider_name


class ProviderAlreadyLoadedError(MCPError):
    """Provider is already loaded."""

    def __init__(self, provider_id: str):
        super().__init__(
            message=f"Provider '{provider_id}' is already loaded",
            provider_id=provider_id,
            operation="load",
        )


class ProviderNotHotLoadedError(MCPError):
    """Cannot unload a provider that was not hot-loaded."""

    def __init__(self, provider_id: str):
        super().__init__(
            message=f"Provider '{provider_id}' was not hot-loaded and cannot be unloaded",
            provider_id=provider_id,
            operation="unload",
        )
