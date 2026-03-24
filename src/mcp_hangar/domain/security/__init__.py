"""
Security module for MCP Hangar.

Provides security primitives including:
- Input validation and sanitization
- Command injection prevention
- Rate limiting
- Secrets management
- Security audit logging utilities
"""

from .input_validator import (
    InputValidator,
    validate_arguments,
    validate_command,
    validate_docker_image,
    validate_environment_variables,
    validate_provider_id,
    validate_timeout,
    validate_tool_name,
    ValidationResult,
)
from .rate_limiter import InMemoryRateLimiter, RateLimitConfig, RateLimiter, RateLimitResult
from .redactor import OutputRedactor, RedactionPattern
from .roles import (
    BUILTIN_ROLES,
    get_builtin_role,
    get_permission,
    list_builtin_roles,
    list_permissions,
    PERMISSIONS,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_DEVELOPER,
    ROLE_PROVIDER_ADMIN,
    ROLE_VIEWER,
)
from .sanitizer import (
    sanitize_command_argument,
    sanitize_environment_value,
    sanitize_log_message,
    sanitize_path,
    Sanitizer,
)
from .secrets import is_sensitive_key, mask_sensitive_value, SecretsMask, SecureEnvironment

__all__ = [
    # Input Validation
    "InputValidator",
    "ValidationResult",
    "validate_provider_id",
    "validate_tool_name",
    "validate_arguments",
    "validate_timeout",
    "validate_command",
    "validate_docker_image",
    "validate_environment_variables",
    # Sanitization
    "Sanitizer",
    "sanitize_command_argument",
    "sanitize_environment_value",
    "sanitize_log_message",
    "sanitize_path",
    # Rate Limiting
    "RateLimiter",
    "RateLimitConfig",
    "InMemoryRateLimiter",
    "RateLimitResult",
    # Secrets
    "SecretsMask",
    "SecureEnvironment",
    "is_sensitive_key",
    "mask_sensitive_value",
    # Redaction
    "OutputRedactor",
    "RedactionPattern",
    # Roles & Permissions
    "BUILTIN_ROLES",
    "PERMISSIONS",
    "ROLE_ADMIN",
    "ROLE_DEVELOPER",
    "ROLE_PROVIDER_ADMIN",
    "ROLE_VIEWER",
    "ROLE_AUDITOR",
    "get_builtin_role",
    "get_permission",
    "list_builtin_roles",
    "list_permissions",
]
