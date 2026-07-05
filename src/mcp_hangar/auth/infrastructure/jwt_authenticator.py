"""JWT/OIDC authentication implementation.

Provides authenticator and token validator for JWT-based authentication
with OIDC support (JWKS validation, standard claims).
"""

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from jwt import PyJWKClient
    from jwt.types import Options

from mcp_hangar.domain.contracts.authentication import AuthRequest, IAuthenticator, ITokenValidator
from mcp_hangar.domain.exceptions import ExpiredCredentialsError, InvalidCredentialsError, TokenLifetimeExceededError
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType

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
        max_token_lifetime: Maximum allowed token lifetime (exp - iat) in seconds.
            Value of 0 means disabled (no lifetime check). Default: 3600.
        require_tenant: Fail-closed multi-tenant gate. When True, a validated token
            whose ``tenant_claim`` is absent or empty is REJECTED instead of being
            silently treated as a global/no-tenant principal. Enable this on
            multi-tenant deployments so a token that does not name a tenant cannot
            act across tenant boundaries. Default False (single-tenant / no-OIDC
            deployments are unaffected).
        strict_tenant_audience: Opt-in strict per-tenant audience binding (RFC 8707).
            When True, after the tenant claim is extracted the token's ``aud`` must
            equal the resource mapped to that tenant in ``tenant_audiences``; a
            mismatch (or a tenant with no mapping) is rejected fail-closed. This
            makes cross-tenant token replay structurally impossible at the token
            layer, independent of the tenant claim. Default False.
        tenant_audiences: Explicit ``{tenant_id: expected_audience}`` map consulted
            only when ``strict_tenant_audience`` is True. The global ``audience`` is
            never used as a fallback for an unmapped tenant.
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

    # Lifetime enforcement
    max_token_lifetime: int = 3600

    # Multi-tenant fail-closed gate
    require_tenant: bool = False

    # Strict per-tenant audience binding (RFC 8707), opt-in
    strict_tenant_audience: bool = False
    tenant_audiences: dict[str, str] = field(default_factory=dict)


class JWTAuthenticator(IAuthenticator):
    """Authenticates requests using JWT tokens (Bearer auth).

    Expects JWT in the Authorization header with 'Bearer' scheme.
    Validates signature, expiration, issuer, and audience.
    """

    def __init__(
        self,
        config: OIDCConfig,
        token_validator: ITokenValidator,
        issuer_configs: dict[str, OIDCConfig] | None = None,
    ):
        """Initialize the JWT authenticator.

        Args:
            config: Default OIDC configuration with issuer, audience, and claim
                mappings. Used for single-issuer setups and as the fallback when a
                validated token's ``iss`` is not in ``issuer_configs``.
            token_validator: Validator for JWT signature and structure (may be a
                multi-issuer validator).
            issuer_configs: Optional per-issuer config map keyed by issuer string.
                When the validated token's ``iss`` matches a key, that issuer's
                claim mappings and lifetime limit are used instead of ``config`` --
                so a multi-issuer registry honors each issuer's own claim names.
        """
        self._config = config
        self._validator = token_validator
        self._issuer_configs = issuer_configs or {}

    def _config_for_claims(self, claims: dict[str, Any]) -> OIDCConfig:
        """Select the OIDC config matching a validated token's ``iss`` claim.

        Falls back to the default ``self._config`` for single-issuer setups or if
        the issuer is not registered (which cannot happen post-validation, since an
        untrusted issuer is already rejected upstream).
        """
        issuer = claims.get("iss")
        if issuer and issuer in self._issuer_configs:
            return self._issuer_configs[issuer]
        return self._config

    def supports(self, request: AuthRequest) -> bool:
        """Check if request has Bearer token.

        ``AuthRequest.headers`` is documented as case-insensitive, but HTTP
        transports normalise header keys to lowercase (``authorization``) while
        callers/tests use the canonical ``Authorization``. Look up both so a real
        Bearer token from the HTTP path is not silently ignored (which would fail
        closed to "no authenticator matched"). Mirrors ApiKeyAuthenticator.
        """
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
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
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""

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

        # Enforce token lifetime before creating principal
        self._enforce_token_lifetime(claims)

        principal = self._claims_to_principal(claims)

        logger.info(
            "jwt_authenticated",
            principal_id=principal.id.value,
            principal_type=principal.type.value,
            issuer=claims.get("iss"),
            source_ip=request.source_ip,
        )

        return principal

    def _enforce_token_lifetime(self, claims: dict[str, Any]) -> None:
        """Enforce maximum token lifetime constraint.

        Args:
            claims: Validated JWT claims containing iat and exp.

        Raises:
            InvalidCredentialsError: If required claims (iat, exp) are missing.
            TokenLifetimeExceededError: If token lifetime exceeds max_token_lifetime.
        """
        config = self._config_for_claims(claims)

        # Skip check if disabled
        if config.max_token_lifetime <= 0:
            return

        # Validate required claims for lifetime check
        if "iat" not in claims:
            raise InvalidCredentialsError(
                message="JWT token missing required 'iat' claim for lifetime validation",
                auth_method="jwt",
            )

        if "exp" not in claims:
            raise InvalidCredentialsError(
                message="JWT token missing required 'exp' claim for lifetime validation",
                auth_method="jwt",
            )

        # Compute token lifetime
        token_lifetime = claims["exp"] - claims["iat"]

        # Enforce maximum lifetime
        if token_lifetime > config.max_token_lifetime:
            raise TokenLifetimeExceededError(
                actual_lifetime=token_lifetime,
                max_lifetime=config.max_token_lifetime,
            )

    def _claims_to_principal(self, claims: dict[str, Any]) -> Principal:
        """Convert JWT claims to Principal.

        Args:
            claims: Validated JWT claims.

        Returns:
            Principal constructed from claims.

        Raises:
            InvalidCredentialsError: If required claims are missing.
        """
        config = self._config_for_claims(claims)

        subject = claims.get(config.subject_claim)
        if not subject:
            raise InvalidCredentialsError(
                message=f"Missing {config.subject_claim} claim in JWT",
                auth_method="jwt",
            )

        groups = claims.get(config.groups_claim, [])
        if isinstance(groups, str):
            groups = [groups]

        tenant_id = claims.get(config.tenant_claim)

        # Fail-closed multi-tenant enforcement (#312): in multi-tenant mode the
        # effective tenant derives SOLELY from this validated claim, so a token
        # that names no tenant must not be admitted as a global/any-tenant
        # principal -- that would let it act across tenant boundaries. Reject an
        # absent or empty tenant claim. Single-tenant / no-OIDC deployments leave
        # ``require_tenant`` False and are unaffected.
        if config.require_tenant and (tenant_id is None or (isinstance(tenant_id, str) and not tenant_id.strip())):
            logger.warning(
                "jwt_missing_tenant_claim",
                tenant_claim=config.tenant_claim,
                issuer=claims.get("iss"),
                subject=subject,
                reason="cross_tenant_rejected",
            )
            raise InvalidCredentialsError(
                message="Missing required tenant claim",
                auth_method="jwt",
            )

        # Strict per-tenant audience binding (#373, RFC 8707): when enabled, the
        # token's `aud` must equal the resource EXPLICITLY mapped to the claimed
        # tenant. This binds each token to one tenant's resource at the token
        # layer, so a token minted for tenant A's resource is rejected when its
        # claim maps to a different resource (or to nothing) -- cross-tenant
        # replay is structurally impossible, independent of the tenant claim.
        # Fail-closed: an unmapped tenant is rejected; the global audience is
        # never a fallback here.
        if config.strict_tenant_audience:
            self._enforce_tenant_audience(claims, tenant_id, subject, config)

        email = claims.get(config.email_claim)

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

    def _enforce_tenant_audience(
        self,
        claims: dict[str, Any],
        tenant_id: Any,
        subject: Any,
        config: OIDCConfig,
    ) -> None:
        """Reject a token whose ``aud`` is not bound to its claimed tenant (#373).

        Emits a ``jwt_cross_tenant_audience`` audit event and raises
        :class:`InvalidCredentialsError` (fail-closed) when the claimed tenant has
        no configured audience mapping, or when the token's ``aud`` does not
        include that tenant's mapped resource.

        Args:
            claims: Validated JWT claims (source of the ``aud`` value).
            tenant_id: The tenant extracted from the validated tenant claim.
            subject: The token subject (for audit context only).
            config: The issuer config carrying the tenant -> audience map.

        Raises:
            InvalidCredentialsError: If the tenant is unmapped or the ``aud`` does
                not match the tenant's mapped resource.
        """
        expected = config.tenant_audiences.get(tenant_id) if isinstance(tenant_id, str) and tenant_id else None
        if not expected:
            # Unmapped tenant (or no tenant claim at all) -> reject fail-closed.
            logger.warning(
                "jwt_cross_tenant_audience",
                reason="tenant_audience_unmapped",
                tenant_id=tenant_id,
                issuer=claims.get("iss"),
                subject=subject,
            )
            raise InvalidCredentialsError(
                message="No audience mapping for tenant",
                auth_method="jwt",
            )

        token_aud = claims.get("aud")
        auds = [token_aud] if isinstance(token_aud, str) else list(token_aud or [])
        if expected not in auds:
            logger.warning(
                "jwt_cross_tenant_audience",
                reason="cross_tenant_audience",
                tenant_id=tenant_id,
                issuer=claims.get("iss"),
                subject=subject,
            )
            raise InvalidCredentialsError(
                message="Token audience does not match tenant resource",
                auth_method="jwt",
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
        self._jwks_client: PyJWKClient | None = None
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
        except ImportError as e:
            raise InvalidCredentialsError(
                message="JWT validation requires PyJWT library. Install with: pip install pyjwt[crypto]",
                auth_method="jwt",
            ) from e

        try:
            # Lazy init JWKS client
            if self._jwks_client is None:
                self._init_jwks_client()

            assert self._jwks_client is not None
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            # In strict per-tenant mode (#373) the token's `aud` names ONE tenant's
            # resource, so signature-time verification must accept any configured
            # tenant resource (PyJWT treats a list as "match at least one"). The
            # precise tenant<->aud binding is then enforced per claim in
            # JWTAuthenticator._enforce_tenant_audience. Otherwise verify against
            # the single global audience exactly as before.
            audience: str | list[str] = self._config.audience
            if self._config.strict_tenant_audience and self._config.tenant_audiences:
                audience = list(dict.fromkeys(self._config.tenant_audiences.values()))

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=audience,
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

        except jwt.ExpiredSignatureError as e:
            raise ExpiredCredentialsError(
                message="JWT token has expired",
                auth_method="jwt",
            ) from e
        except jwt.InvalidAudienceError as e:
            raise InvalidCredentialsError(
                message="Invalid JWT audience",
                auth_method="jwt",
            ) from e
        except jwt.InvalidIssuerError as e:
            raise InvalidCredentialsError(
                message="Invalid JWT issuer",
                auth_method="jwt",
            ) from e
        except jwt.InvalidTokenError as e:
            raise InvalidCredentialsError(
                message=f"Invalid JWT token: {e}",
                auth_method="jwt",
            ) from e

    def _init_jwks_client(self) -> None:
        """Initialize JWKS client, discovering URI if needed."""
        try:
            import httpx
            import jwt
        except ImportError as e:
            raise InvalidCredentialsError(
                message=f"JWT validation requires additional libraries: {e}",
                auth_method="jwt",
            ) from e

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
                ) from e

        self._jwks_uri = jwks_uri
        self._jwks_client = jwt.PyJWKClient(jwks_uri)


class MultiIssuerTokenValidator(ITokenValidator):
    """Routes JWT validation to a per-issuer validator by the token's ``iss`` claim.

    Trusts MULTIPLE issuers. Each wrapped :class:`JWKSTokenValidator` keeps its own
    OIDC configuration (issuer, audience, JWKS client). Tokens are dispatched to the
    matching validator based on their ``iss`` claim, and full signature, issuer,
    audience, and lifetime verification happens inside that validator unchanged.

    Fail-closed contract (this is a security trust boundary):
        * A token whose ``iss`` claim is missing, empty, or not a registered issuer
          is rejected with :class:`InvalidCredentialsError` — even if it is otherwise
          well-formed and correctly signed.
        * There is NO default/fallback issuer. An unrecognized issuer never reaches
          any wrapped validator.
        * The set of trusted issuers is never leaked in error messages.
    """

    def __init__(self, validators: list[JWKSTokenValidator]):
        """Initialize the multi-issuer validator.

        Args:
            validators: Per-issuer JWKS validators. The registry is keyed by each
                validator's configured issuer (``validator._config.issuer``); the
                ``iss`` claim of an incoming token is matched against these keys.
        """
        self._validators: dict[str, JWKSTokenValidator] = {
            validator._config.issuer: validator for validator in validators
        }

    def validate(self, token: str) -> dict:
        """Validate a JWT by routing it to the validator for its ``iss`` claim.

        Args:
            token: The JWT string to validate.

        Returns:
            Dictionary of validated claims from the matching per-issuer validator.

        Raises:
            InvalidCredentialsError: If the token cannot be decoded, has no ``iss``
                claim, or names an issuer that is not registered (fail-closed).
            ExpiredCredentialsError: If the matching validator finds the token expired.
        """
        try:
            import jwt
        except ImportError as e:
            raise InvalidCredentialsError(
                message="JWT validation requires PyJWT library. Install with: pip install pyjwt[crypto]",
                auth_method="jwt",
            ) from e

        # Read the unverified 'iss' claim only to select a validator. Signature
        # verification is intentionally deferred to the chosen validator.
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.InvalidTokenError as e:
            raise InvalidCredentialsError(
                message="Invalid JWT token",
                auth_method="jwt",
            ) from e

        issuer = unverified.get("iss")
        # Route only on a non-empty string iss. A non-string iss (JSON array/object)
        # is unhashable and would raise TypeError from dict.get -> guard so it fails
        # closed as a clean 401 instead of escaping as a 500.
        validator = self._validators.get(issuer) if isinstance(issuer, str) and issuer else None

        if validator is None:
            # Fail-closed: missing/empty/non-string or unrecognized issuer is rejected
            # without disclosing the set of trusted issuers.
            logger.warning("jwt_unknown_issuer", issuer=issuer)
            raise InvalidCredentialsError(
                message="Untrusted JWT issuer",
                auth_method="jwt",
            )

        return validator.validate(token)


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
        except ImportError as e:
            raise InvalidCredentialsError(
                message="JWT validation requires PyJWT library",
                auth_method="jwt",
            ) from e

        options: Options = {
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
        except jwt.ExpiredSignatureError as e:
            raise ExpiredCredentialsError(
                message="JWT token has expired",
                auth_method="jwt",
            ) from e
        except jwt.InvalidTokenError as e:
            raise InvalidCredentialsError(
                message=f"Invalid JWT token: {e}",
                auth_method="jwt",
            ) from e
