"""Common value objects used across the domain.

Contains:
- CorrelationId - request tracing
- ToolName - tool identification
- ToolArguments - validated tool arguments
- Multi-tenancy value objects (TenantId, NamespaceId, CatalogItemId, ResourceScope)
"""

from dataclasses import dataclass
import re
from typing import Any
import uuid


class ToolName:
    """Name of a tool provided by a provider.

    Validates tool names with the following rules:
    - Non-empty string
    - Alphanumeric, hyphens, underscores, dots allowed (for namespaced tools)
    - Max 128 characters

    Attributes:
        value: The validated tool name string.

    Raises:
        ValueError: If the provided value violates validation rules.

    Example:
        >>> tool = ToolName("math.add")
        >>> str(tool)
        'math.add'
    """

    _VALID_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+$")
    _MAX_LENGTH = 128

    def __init__(self, value: str):
        """Initialize ToolName with validation.

        Args:
            value: The tool name string to validate.

        Raises:
            ValueError: If value is empty, too long, or contains invalid characters.
        """
        if not value:
            raise ValueError("ToolName cannot be empty")
        if len(value) > self._MAX_LENGTH:
            raise ValueError(f"ToolName cannot exceed {self._MAX_LENGTH} characters")
        if not self._VALID_PATTERN.match(value):
            raise ValueError("ToolName must contain only alphanumeric characters, hyphens, underscores, and dots")
        self._value = value

    @property
    def value(self) -> str:
        """Get the raw tool name string."""
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"ToolName('{self._value}')"

    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            return self._value == other
        if not isinstance(other, ToolName):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class CorrelationId:
    """Correlation ID for tracing requests.

    Rules:
    - Non-empty string
    - Valid UUID v4 format (or auto-generated)
    """

    value: str

    def __init__(self, value: str | None = None):
        if value is None:
            # Generate new UUID
            value = str(uuid.uuid4())
        else:
            # Validate existing UUID
            if not value:
                raise ValueError("CorrelationId cannot be empty")
            try:
                uuid.UUID(value, version=4)
            except ValueError as e:
                raise ValueError("CorrelationId must be a valid UUID v4") from e

        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"CorrelationId('{self.value}')"


class ToolArguments:
    """Validated tool invocation arguments.

    Rules:
    - Must be a dictionary
    - Size limited to prevent DoS
    - Keys must be strings
    """

    MAX_SIZE_BYTES = 1_000_000  # 1MB limit
    MAX_DEPTH = 10  # Maximum nesting depth

    def __init__(self, arguments: dict[str, Any]):
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a dictionary")

        self._validate_size(arguments)
        self._validate_structure(arguments)
        self._arguments = arguments

    def _validate_size(self, arguments: dict[str, Any]) -> None:
        """Validate arguments don't exceed size limit."""
        import json

        try:
            size = len(json.dumps(arguments))
            if size > self.MAX_SIZE_BYTES:
                raise ValueError(f"Tool arguments exceed maximum size ({size} > {self.MAX_SIZE_BYTES} bytes)")
        except (TypeError, ValueError) as e:
            if "size" not in str(e):
                raise ValueError(f"Tool arguments must be JSON-serializable: {e}") from e
            raise

    def _validate_structure(self, obj: Any, depth: int = 0) -> None:
        """Validate argument structure and depth."""
        if depth > self.MAX_DEPTH:
            raise ValueError(f"Tool arguments exceed maximum nesting depth ({self.MAX_DEPTH})")

        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    raise ValueError("Tool argument keys must be strings")
                self._validate_structure(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._validate_structure(item, depth + 1)

    @property
    def value(self) -> dict[str, Any]:
        """Get the validated arguments."""
        return self._arguments

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (returns a copy)."""
        return dict(self._arguments)

    def __getitem__(self, key: str) -> Any:
        return self._arguments[key]

    def __contains__(self, key: str) -> bool:
        return key in self._arguments

    def get(self, key: str, default: Any = None) -> Any:
        return self._arguments.get(key, default)


# --- Multi-Tenancy Value Objects ---


@dataclass(frozen=True)
class TenantId:
    """Unique identifier for a tenant.

    Tenants represent teams or business units with isolated resources.
    TenantIds must follow Kubernetes naming conventions for compatibility.

    Attributes:
        value: Alphanumeric string starting with letter (1-63 chars)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 63:
            raise ValueError("TenantId must be 1-63 characters")
        if not self.value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("TenantId must be alphanumeric with - or _")
        if not self.value[0].isalpha():
            raise ValueError("TenantId must start with a letter")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class NamespaceId:
    """Unique identifier for a namespace within a tenant.

    Namespaces provide workload isolation within a tenant.
    Follows Kubernetes namespace naming rules.

    Attributes:
        value: Alphanumeric string with hyphens (1-63 chars)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 63:
            raise ValueError("NamespaceId must be 1-63 characters")
        if not self.value.replace("-", "").isalnum():
            raise ValueError("NamespaceId must be alphanumeric with -")
        if not self.value[0].isalnum() or not self.value[-1].isalnum():
            raise ValueError("NamespaceId must start and end with alphanumeric")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CatalogItemId:
    """Unique identifier for a catalog item.

    Catalog items are provider templates that can be deployed.

    Attributes:
        value: Identifier string (1-128 characters)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 128:
            raise ValueError("CatalogItemId must be 1-128 characters")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ResourceScope:
    """Scope for resource access control.

    Defines hierarchical scope for authorization:
    - Organization (all tenants)
    - Tenant (specific tenant + all its namespaces)
    - Namespace (specific tenant + specific namespace)

    Attributes:
        organization_id: Optional organization identifier
        tenant_id: Optional tenant identifier
        namespace_id: Optional namespace identifier
    """

    organization_id: str | None = None
    tenant_id: str | None = None
    namespace_id: str | None = None

    def includes(self, other: "ResourceScope") -> bool:
        """Check if this scope includes another scope.

        Organization scope includes all.
        Tenant scope includes matching tenant.
        Namespace scope requires exact match.

        Args:
            other: Scope to check

        Returns:
            True if this scope includes the other scope
        """
        # Organization scope includes all
        if self.tenant_id is None and self.namespace_id is None:
            return True

        # Tenant scope includes matching tenant
        if self.tenant_id and other.tenant_id != self.tenant_id:
            return False

        # Namespace scope requires exact match
        if self.namespace_id and other.namespace_id != self.namespace_id:
            return False

        return True

    def __str__(self) -> str:
        if self.namespace_id:
            return f"namespace:{self.tenant_id}/{self.namespace_id}"
        if self.tenant_id:
            return f"tenant:{self.tenant_id}"
        return "organization"
