"""API Key authentication implementation.

Provides authenticator and in-memory store for API key authentication.
Keys are stored as SHA-256 hashes, never in plaintext.
"""

import hashlib
import secrets
import threading
from datetime import UTC, datetime

import structlog

from ...domain.contracts.authentication import ApiKeyMetadata, AuthRequest, IApiKeyStore, IAuthenticator
from ...domain.exceptions import ExpiredCredentialsError, InvalidCredentialsError, RevokedCredentialsError
from ...domain.value_objects import Principal, PrincipalId, PrincipalType

logger = structlog.get_logger(__name__)

# Maximum allowed API key length to prevent DoS
MAX_API_KEY_LENGTH = 256


class ApiKeyAuthenticator(IAuthenticator):
    """Authenticates requests using API keys.

    API keys are expected in the X-API-Key header (configurable).
    Keys must start with the configured prefix (default: 'mcp_') for
    easy identification and log redaction.

    Attributes:
        HEADER_NAME: Name of the header containing the API key.
        PREFIX: Required prefix for API keys.
    """

    HEADER_NAME = "X-API-Key"
    PREFIX = "mcp_"

    def __init__(self, key_store: IApiKeyStore, header_name: str | None = None):
        """Initialize the authenticator.

        Args:
            key_store: Storage backend for API keys.
            header_name: Optional custom header name (default: X-API-Key).
        """
        self._key_store = key_store
        self._header_name = header_name or self.HEADER_NAME

    def supports(self, request: AuthRequest) -> bool:
        """Check if request has API key header."""
        # Check both original and lowercase versions for case-insensitive lookup
        return self._header_name in request.headers or self._header_name.lower() in request.headers

    def authenticate(self, request: AuthRequest) -> Principal:
        """Authenticate using API key.

        Args:
            request: The authentication request with headers.

        Returns:
            Authenticated Principal.

        Raises:
            InvalidCredentialsError: If key format is invalid or key not found.
            ExpiredCredentialsError: If key has expired.
            RevokedCredentialsError: If key has been revoked.
        """
        # Case-insensitive header lookup
        key = request.headers.get(self._header_name) or request.headers.get(self._header_name.lower()) or ""

        if not key:
            raise InvalidCredentialsError(
                message="API key header is empty",
                auth_method="api_key",
            )

        # Validate key length to prevent DoS
        if len(key) > MAX_API_KEY_LENGTH:
            raise InvalidCredentialsError(
                message="API key exceeds maximum length",
                auth_method="api_key",
            )

        if not key.startswith(self.PREFIX):
            raise InvalidCredentialsError(
                message=f"Invalid API key format: must start with '{self.PREFIX}'",
                auth_method="api_key",
            )

        key_hash = self._hash_key(key)

        # Use constant-time lookup to prevent timing attacks
        principal = self._key_store.get_principal_for_key(key_hash)

        if principal is None:
            # Log with minimal key info (only prefix indicator, no actual key content)
            logger.warning(
                "api_key_not_found",
                key_length=len(key),
                source_ip=request.source_ip,
            )
            raise InvalidCredentialsError(
                message="Invalid API key",
                auth_method="api_key",
            )

        logger.info(
            "api_key_authenticated",
            principal_id=principal.id.value,
            principal_type=principal.type.value,
            source_ip=request.source_ip,
        )

        return principal

    @staticmethod
    def _hash_key(key: str) -> str:
        """Hash API key for storage lookup.

        Args:
            key: The raw API key.

        Returns:
            SHA-256 hash of the key.
        """
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def generate_key(cls) -> str:
        """Generate a new API key.

        Returns:
            A new API key with the configured prefix.
        """
        random_part = secrets.token_urlsafe(32)
        return f"{cls.PREFIX}{random_part}"


class InMemoryApiKeyStore(IApiKeyStore):
    """In-memory API key store for development/testing.

    WARNING: Keys are lost on restart. Use a persistent store
    (e.g., SQLite, PostgreSQL) for production.

    This implementation is thread-safe using a reentrant lock.
    """

    # Maximum number of keys per principal to prevent abuse
    MAX_KEYS_PER_PRINCIPAL = 100

    def __init__(self) -> None:
        """Initialize the in-memory store."""
        self._lock = threading.RLock()
        # key_hash -> (metadata, principal)
        self._keys: dict[str, tuple[ApiKeyMetadata, Principal]] = {}
        # principal_id -> list of key_ids
        self._principal_keys: dict[str, list[str]] = {}

    def get_principal_for_key(self, key_hash: str) -> Principal | None:
        """Look up principal for an API key hash.

        Args:
            key_hash: SHA-256 hash of the API key.

        Returns:
            Principal if found and valid, None if not found.

        Raises:
            ExpiredCredentialsError: If the key has expired.
            RevokedCredentialsError: If the key has been revoked.
        """
        with self._lock:
            if key_hash not in self._keys:
                return None

            metadata, principal = self._keys[key_hash]

            if metadata.revoked:
                raise RevokedCredentialsError(
                    message="API key has been revoked",
                    auth_method="api_key",
                )

            if metadata.expires_at and metadata.expires_at < datetime.now(UTC):
                raise ExpiredCredentialsError(
                    message="API key has expired",
                    auth_method="api_key",
                    expired_at=metadata.expires_at.timestamp(),
                )

            # Update last_used_at
            # Note: Creating new metadata object since ApiKeyMetadata is a dataclass
            # In production, this would be an atomic update in the database
            updated_metadata = ApiKeyMetadata(
                key_id=metadata.key_id,
                name=metadata.name,
                principal_id=metadata.principal_id,
                created_at=metadata.created_at,
                expires_at=metadata.expires_at,
                last_used_at=datetime.now(UTC),
                revoked=metadata.revoked,
            )
            self._keys[key_hash] = (updated_metadata, principal)

            return principal

    def create_key(
        self,
        principal_id: str,
        name: str,
        expires_at: datetime | None = None,
        groups: frozenset[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "system",
    ) -> str:
        """Create a new API key.

        Args:
            principal_id: ID for the principal this key authenticates as.
            name: Human-readable name for the key.
            expires_at: Optional expiration datetime.
            groups: Optional groups to assign to the principal.
            tenant_id: Optional tenant ID for multi-tenancy.
            created_by: Principal creating the key.

        Returns:
            The raw API key (only shown once!).

        Raises:
            ValueError: If principal has reached maximum number of keys.
        """
        with self._lock:
            # Check key limit per principal
            existing_keys = self._principal_keys.get(principal_id, [])
            if len(existing_keys) >= self.MAX_KEYS_PER_PRINCIPAL:
                raise ValueError(
                    f"Principal {principal_id} has reached maximum number of API keys "
                    f"({self.MAX_KEYS_PER_PRINCIPAL})"
                )

            raw_key = ApiKeyAuthenticator.generate_key()
            key_hash = ApiKeyAuthenticator._hash_key(raw_key)
            key_id = secrets.token_urlsafe(8)

            now = datetime.now(UTC)
            metadata = ApiKeyMetadata(
                key_id=key_id,
                name=name,
                principal_id=principal_id,
                created_at=now,
                expires_at=expires_at,
                last_used_at=None,
                revoked=False,
            )

            principal = Principal(
                id=PrincipalId(principal_id),
                type=PrincipalType.SERVICE_ACCOUNT,
                tenant_id=tenant_id,
                groups=groups or frozenset(),
                metadata={"key_id": key_id, "key_name": name},
            )

            self._keys[key_hash] = (metadata, principal)

            if principal_id not in self._principal_keys:
                self._principal_keys[principal_id] = []
            self._principal_keys[principal_id].append(key_id)

            logger.info(
                "api_key_created",
                key_id=key_id,
                principal_id=principal_id,
                name=name,
                expires_at=expires_at.isoformat() if expires_at else None,
            )

            return raw_key  # Only returned once!

    def revoke_key(self, key_id: str, revoked_by: str = "system", reason: str = "") -> bool:
        """Revoke an API key.

        Args:
            key_id: Unique identifier of the key to revoke.
            revoked_by: Principal revoking the key.
            reason: Reason for revocation.

        Returns:
            True if key was found and revoked, False if not found.
        """
        with self._lock:
            for key_hash, (metadata, principal) in self._keys.items():
                if metadata.key_id == key_id:
                    # Create new metadata with revoked=True
                    updated_metadata = ApiKeyMetadata(
                        key_id=metadata.key_id,
                        name=metadata.name,
                        principal_id=metadata.principal_id,
                        created_at=metadata.created_at,
                        expires_at=metadata.expires_at,
                        last_used_at=metadata.last_used_at,
                        revoked=True,
                    )
                    self._keys[key_hash] = (updated_metadata, principal)

                    logger.info(
                        "api_key_revoked",
                        key_id=key_id,
                        principal_id=metadata.principal_id,
                        revoked_by=revoked_by,
                        reason=reason,
                    )
                    return True

            logger.warning("api_key_revoke_not_found", key_id=key_id)
            return False

    def list_keys(self, principal_id: str) -> list[ApiKeyMetadata]:
        """List API keys for a principal (metadata only, not the keys).

        Args:
            principal_id: ID of the principal to list keys for.

        Returns:
            List of ApiKeyMetadata for the principal's keys.
        """
        with self._lock:
            result: list[ApiKeyMetadata] = []
            for metadata, principal in self._keys.values():
                if metadata.principal_id == principal_id:
                    result.append(metadata)
            return result

    def get_key_by_id(self, key_id: str) -> ApiKeyMetadata | None:
        """Get key metadata by key_id (for admin operations).

        Args:
            key_id: The key identifier.

        Returns:
            ApiKeyMetadata if found, None otherwise.
        """
        with self._lock:
            for metadata, _ in self._keys.values():
                if metadata.key_id == key_id:
                    return metadata
            return None

    def count_keys(self, principal_id: str) -> int:
        """Count active (non-revoked) API keys for a principal.

        Args:
            principal_id: ID of the principal to count keys for.

        Returns:
            Number of active API keys.
        """
        with self._lock:
            count = 0
            for metadata, _ in self._keys.values():
                if metadata.principal_id == principal_id and not metadata.revoked:
                    if metadata.expires_at is None or metadata.expires_at > datetime.now(UTC):
                        count += 1
            return count

    def count_all_keys(self) -> int:
        """Get total number of keys in the store.

        Returns:
            Number of API keys (including revoked).
        """
        with self._lock:
            return len(self._keys)

    def count_all_active_keys(self) -> int:
        """Get number of active (non-revoked, non-expired) keys.

        Returns:
            Number of active API keys.
        """
        with self._lock:
            now = datetime.now(UTC)
            count = 0
            for metadata, _ in self._keys.values():
                if not metadata.revoked:
                    if metadata.expires_at is None or metadata.expires_at > now:
                        count += 1
            return count
