"""Authentication contracts (ports) for the domain layer.

These protocols define the interfaces for authentication components.
Infrastructure layer provides concrete implementations.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ..value_objects import Principal


@dataclass
class AuthRequest:
    """Normalized authentication request.

    Contains all information needed to authenticate a request,
    abstracted from the transport layer (HTTP, gRPC, etc.).

    Attributes:
        headers: Request headers as a case-insensitive dict.
        source_ip: IP address of the request origin.
        method: HTTP method or equivalent (GET, POST, etc.).
        path: Request path/endpoint.
        metadata: Additional context for authentication decisions.
    """

    headers: dict[str, str]
    source_ip: str
    method: str = ""
    path: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ApiKeyMetadata:
    """Metadata about an API key (never contains the actual key).

    Attributes:
        key_id: Unique identifier for the key (not the key itself).
        name: Human-readable name for the key.
        principal_id: Principal ID this key authenticates as.
        created_at: When the key was created.
        expires_at: Optional expiration datetime.
        last_used_at: When the key was last used for authentication.
        revoked: Whether the key has been revoked.
    """

    key_id: str
    name: str
    principal_id: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked: bool = False

    @property
    def is_expired(self) -> bool:
        """Check if the key has expired."""
        if self.expires_at is None:
            return False

        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if the key is valid (not revoked and not expired)."""
        return not self.revoked and not self.is_expired


@runtime_checkable
class IAuthenticator(Protocol):
    """Extracts and validates credentials from a request.

    Authenticators are responsible for:
    1. Detecting if they can handle a request (supports())
    2. Extracting credentials from the request
    3. Validating credentials
    4. Returning an authenticated Principal

    Multiple authenticators can be chained, with the first supporting
    authenticator handling the request.
    """

    @abstractmethod
    def authenticate(self, request: AuthRequest) -> Principal:
        """Authenticate a request and return the principal.

        Args:
            request: The incoming request with credentials.

        Returns:
            Authenticated Principal.

        Raises:
            InvalidCredentialsError: If credentials are invalid.
            ExpiredCredentialsError: If credentials have expired.
            RevokedCredentialsError: If credentials have been revoked.
            MissingCredentialsError: If required credentials are missing.
        """
        ...

    @abstractmethod
    def supports(self, request: AuthRequest) -> bool:
        """Check if this authenticator can handle the request.

        Args:
            request: The incoming request to check.

        Returns:
            True if this authenticator should handle the request.
        """
        ...


@runtime_checkable
class ITokenValidator(Protocol):
    """Validates tokens (JWT, API keys, etc.).

    Token validators handle the cryptographic verification of tokens
    without the transport-layer concerns of extracting them from requests.
    """

    @abstractmethod
    def validate(self, token: str) -> dict:
        """Validate token and return claims.

        Args:
            token: The token string to validate.

        Returns:
            Dictionary of validated claims from the token.

        Raises:
            InvalidCredentialsError: If token is invalid or malformed.
            ExpiredCredentialsError: If token has expired.
        """
        ...


@runtime_checkable
class IApiKeyStore(Protocol):
    """Storage for API keys.

    Handles CRUD operations for API keys. Keys are stored as hashes,
    never in plaintext. The raw key is only returned once during creation.
    """

    @abstractmethod
    def get_principal_for_key(self, key_hash: str) -> Principal | None:
        """Look up principal for an API key hash.

        Args:
            key_hash: SHA-256 hash of the API key.

        Returns:
            Principal associated with the key, or None if not found.

        Raises:
            ExpiredCredentialsError: If the key has expired.
            RevokedCredentialsError: If the key has been revoked.
        """
        ...

    @abstractmethod
    def create_key(
        self,
        principal_id: str,
        name: str,
        expires_at: datetime | None = None,
        groups: frozenset[str] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Create a new API key.

        Args:
            principal_id: ID for the principal this key authenticates as.
            name: Human-readable name for the key.
            expires_at: Optional expiration datetime.
            groups: Optional groups to assign to the principal.
            tenant_id: Optional tenant ID for multi-tenancy.

        Returns:
            The raw API key (only shown once!).
        """
        ...

    @abstractmethod
    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key.

        Args:
            key_id: Unique identifier of the key to revoke.

        Returns:
            True if key was found and revoked, False if not found.
        """
        ...

    @abstractmethod
    def list_keys(self, principal_id: str) -> list[ApiKeyMetadata]:
        """List API keys for a principal (metadata only, not the keys).

        Args:
            principal_id: ID of the principal to list keys for.

        Returns:
            List of ApiKeyMetadata for the principal's keys.
        """
        ...

    @abstractmethod
    def count_keys(self, principal_id: str) -> int:
        """Count active (non-revoked) API keys for a principal.

        Args:
            principal_id: ID of the principal to count keys for.

        Returns:
            Number of active API keys.
        """
        ...
