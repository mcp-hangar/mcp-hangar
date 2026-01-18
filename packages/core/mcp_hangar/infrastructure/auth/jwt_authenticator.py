"""JWT/OIDC authentication implementation.

Provides authenticator and token validator for JWT-based authentication
with OIDC support (JWKS validation, standard claims).
"""

from dataclasses import dataclass
from typing import Any

import structlog

from ...domain.contracts.authentication import AuthRequest, IAuthenticator, ITokenValidator
from ...domain.exceptions import ExpiredCredentialsError, InvalidCredentialsError
from ...domain.value_objects import Principal, PrincipalId, PrincipalType

logger = structlog.get_logger(__name__)


@dataclass
class OIDCConfig:
    """OIDC provider configuration.

    Attributes:
        issuer: OIDC issuer URL (e.g., https://auth.company.com).
        audience: Expected audience claim value.
        jwks_uri: JWKS endpoint URL (auto-discovered if None).
        client_id: Optional client ID for additional validation.
        subject_claim: JWT claim for subject (default: sub).
        groups_claim: JWT claim for groups (default: groups).
        tenant_claim: JWT claim for tenant ID (default: tenant_id).
        email_claim: JWT claim for email (default: email).
    """

    issuer: str
    audience: str
    jwks_uri: str | None = None
    client_id: str | None = None

    # Claim mappings
    subject_claim: str = "sub"
    groups_claim: str = "groups"
    tenant_claim: str = "tenant_id"
    email_claim: str = "email"


class JWTAuthenticator(IAuthenticator):
    """Authenticates requests using JWT tokens (Bearer auth).

    Expects JWT in the Authorization header with 'Bearer' scheme.
    Validates signature, expiration, issuer, and audience.
    """

    def __init__(self, config: OIDCConfig, token_validator: ITokenValidator):
        """Initialize the JWT authenticator.

        Args:
            config: OIDC configuration with issuer, audience, and claim mappings.
            token_validator: Validator for JWT signature and structure.
        """
        self._config = config
        self._validator = token_validator

    def supports(self, request: AuthRequest) -> bool:
        """Check if request has Bearer token."""
        auth_header = request.headers.get("Authorization", "")
        return auth_header.startswith("Bearer ")

    def authenticate(self, request: AuthRequest) -> Principal:
        """Authenticate using JWT token.

        Args:
            request: The authentication request with headers.

        Returns:
            Authenticated Principal extracted from JWT claims.

        Raises:
            InvalidCredentialsError: If token is invalid or malformed.
            ExpiredCredentialsError: If token has expired.
        """
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            raise InvalidCredentialsError(
                message="Missing Bearer token",
                auth_method="jwt",
            )

        token = auth_header[7:]  # Remove "Bearer " prefix

        if not token:
            raise InvalidCredentialsError(
                message="Empty Bearer token",
                auth_method="jwt",
            )

        claims = self._validator.validate(token)

        principal = self._claims_to_principal(claims)

        logger.info(
            "jwt_authenticated",
            principal_id=principal.id.value,
            principal_type=principal.type.value,
            issuer=claims.get("iss"),
            source_ip=request.source_ip,
        )

        return principal

    def _claims_to_principal(self, claims: dict[str, Any]) -> Principal:
        """Convert JWT claims to Principal.

        Args:
            claims: Validated JWT claims.

        Returns:
            Principal constructed from claims.

        Raises:
            InvalidCredentialsError: If required claims are missing.
        """
        subject = claims.get(self._config.subject_claim)
        if not subject:
            raise InvalidCredentialsError(
                message=f"Missing {self._config.subject_claim} claim in JWT",
                auth_method="jwt",
            )

        groups = claims.get(self._config.groups_claim, [])
        if isinstance(groups, str):
            groups = [groups]

        tenant_id = claims.get(self._config.tenant_claim)
        email = claims.get(self._config.email_claim)

        return Principal(
            id=PrincipalId(subject),
            type=PrincipalType.USER,
            tenant_id=tenant_id,
            groups=frozenset(groups) if groups else frozenset(),
            metadata={
                "email": email,
                "issuer": claims.get("iss"),
                "issued_at": claims.get("iat"),
                "expires_at": claims.get("exp"),
            },
        )


class JWKSTokenValidator(ITokenValidator):
    """Validates JWT tokens using JWKS (JSON Web Key Set).

    Lazily initializes the JWKS client on first validation.
    Supports RS256 and ES256 algorithms.

    Note: Requires PyJWT library to be installed.
    """

    def __init__(self, config: OIDCConfig):
        """Initialize the JWKS validator.

        Args:
            config: OIDC configuration with issuer and optional JWKS URI.
        """
        self._config = config
        self._jwks_client = None
        self._jwks_uri: str | None = None

    def validate(self, token: str) -> dict:
        """Validate JWT and return claims.

        Args:
            token: The JWT string to validate.

        Returns:
            Dictionary of validated claims.

        Raises:
            InvalidCredentialsError: If token is invalid or malformed.
            ExpiredCredentialsError: If token has expired.
        """
        try:
            import jwt
        except ImportError:
            raise InvalidCredentialsError(
                message="JWT validation requires PyJWT library. Install with: pip install pyjwt[crypto]",
                auth_method="jwt",
            )

        try:
            # Lazy init JWKS client
            if self._jwks_client is None:
                self._init_jwks_client()

            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._config.audience,
                issuer=self._config.issuer,
                options={
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_nbf": True,  # Verify 'not before' claim
                },
            )

            return claims

        except jwt.ExpiredSignatureError:
            raise ExpiredCredentialsError(
                message="JWT token has expired",
                auth_method="jwt",
            )
        except jwt.InvalidAudienceError:
            raise InvalidCredentialsError(
                message="Invalid JWT audience",
                auth_method="jwt",
            )
        except jwt.InvalidIssuerError:
            raise InvalidCredentialsError(
                message="Invalid JWT issuer",
                auth_method="jwt",
            )
        except jwt.InvalidTokenError as e:
            raise InvalidCredentialsError(
                message=f"Invalid JWT token: {e}",
                auth_method="jwt",
            )

    def _init_jwks_client(self) -> None:
        """Initialize JWKS client, discovering URI if needed."""
        try:
            import httpx
            import jwt
        except ImportError as e:
            raise InvalidCredentialsError(
                message=f"JWT validation requires additional libraries: {e}",
                auth_method="jwt",
            )

        # Security check: OIDC issuer should use HTTPS in production
        if not self._config.issuer.startswith("https://"):
            logger.warning(
                "oidc_issuer_not_https",
                issuer=self._config.issuer,
                warning="OIDC issuer should use HTTPS to prevent MITM attacks",
            )

        jwks_uri = self._config.jwks_uri

        if not jwks_uri:
            # Discover from OIDC well-known endpoint
            discovery_url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
            try:
                response = httpx.get(discovery_url, timeout=10)
                response.raise_for_status()
                oidc_config = response.json()
                jwks_uri = oidc_config.get("jwks_uri")

                if not jwks_uri:
                    raise InvalidCredentialsError(
                        message="OIDC discovery did not return jwks_uri",
                        auth_method="jwt",
                    )

                # Security check: JWKS URI should also use HTTPS
                if not jwks_uri.startswith("https://"):
                    logger.warning(
                        "jwks_uri_not_https",
                        jwks_uri=jwks_uri,
                        warning="JWKS URI should use HTTPS to prevent key tampering",
                    )

                logger.info(
                    "oidc_discovery_complete",
                    issuer=self._config.issuer,
                    jwks_uri=jwks_uri,
                )
            except httpx.HTTPError as e:
                raise InvalidCredentialsError(
                    message=f"Failed to discover OIDC configuration: {e}",
                    auth_method="jwt",
                )

        self._jwks_uri = jwks_uri
        self._jwks_client = jwt.PyJWKClient(jwks_uri)


class StaticSecretTokenValidator(ITokenValidator):
    """Simple JWT validator using a static secret (HS256).

    WARNING: Only for development/testing. Use JWKS in production.
    """

    def __init__(self, secret: str, issuer: str | None = None, audience: str | None = None):
        """Initialize with a static secret.

        Args:
            secret: The HMAC secret for HS256 validation.
            issuer: Optional expected issuer.
            audience: Optional expected audience.
        """
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    def validate(self, token: str) -> dict:
        """Validate JWT using static secret.

        Args:
            token: The JWT string to validate.

        Returns:
            Dictionary of validated claims.

        Raises:
            InvalidCredentialsError: If token is invalid.
            ExpiredCredentialsError: If token has expired.
        """
        try:
            import jwt
        except ImportError:
            raise InvalidCredentialsError(
                message="JWT validation requires PyJWT library",
                auth_method="jwt",
            )

        options = {
            "verify_exp": True,
            "verify_iat": True,
            "verify_aud": self._audience is not None,
            "verify_iss": self._issuer is not None,
        }

        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options=options,
            )
            return claims
        except jwt.ExpiredSignatureError:
            raise ExpiredCredentialsError(
                message="JWT token has expired",
                auth_method="jwt",
            )
        except jwt.InvalidTokenError as e:
            raise InvalidCredentialsError(
                message=f"Invalid JWT token: {e}",
                auth_method="jwt",
            )
