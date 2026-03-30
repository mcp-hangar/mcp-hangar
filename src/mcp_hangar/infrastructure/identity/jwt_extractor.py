"""JWT-based identity extractor.

Extracts caller identity from a JWT (JSON Web Token) in the Authorization
header. Validates token signature and expiry. Supports both symmetric (HS256)
and asymmetric (RS256/ES256) algorithms via PyJWT.

The JWT claims mapping is configurable:
    sub       -> CallerIdentity.user_id
    agent_id  -> CallerIdentity.agent_id  (custom claim)
    sid       -> CallerIdentity.session_id
    type      -> CallerIdentity.principal_type
    jti       -> IdentityContext.correlation_id
"""

from __future__ import annotations

from typing import Any, Dict

import structlog

from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

logger = structlog.get_logger(__name__)


class JWTIdentityExtractor:
    """Extracts identity from a JWT Bearer token.

    Implements `IIdentityExtractor` protocol.

    Usage::

        extractor = JWTIdentityExtractor(
            secret_or_key="my-secret-or-pem",
            algorithms=["HS256"],
        )
        ctx = extractor.extract({"authorization": "Bearer eyJ..."})

    If PyJWT is not installed, extraction always returns None
    (soft dependency — identity features degrade gracefully).
    """

    def __init__(
        self,
        *,
        secret_or_key: str | bytes,
        algorithms: list[str] | None = None,
        audience: str | None = None,
        issuer: str | None = None,
        # Claim name mapping
        user_id_claim: str = "sub",
        agent_id_claim: str = "agent_id",
        session_id_claim: str = "sid",
        principal_type_claim: str = "type",
        correlation_id_claim: str = "jti",
    ) -> None:
        self._secret_or_key = secret_or_key
        self._algorithms = algorithms or ["HS256"]
        self._audience = audience
        self._issuer = issuer
        # Claim mapping
        self._user_id_claim = user_id_claim
        self._agent_id_claim = agent_id_claim
        self._session_id_claim = session_id_claim
        self._principal_type_claim = principal_type_claim
        self._correlation_id_claim = correlation_id_claim

    def extract(
        self,
        metadata: list[tuple[str, str]] | Dict[str, str] | None,
    ) -> IdentityContext | None:
        """Extract identity from JWT in Authorization header.

        Args:
            metadata: Headers containing an Authorization Bearer token.

        Returns:
            IdentityContext on success, None if no token or validation fails.
        """
        if metadata is None:
            return None

        headers = self._normalize_headers(metadata)
        auth_header = headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return None

        token = auth_header[7:].strip()
        if not token:
            return None

        claims = self._decode_token(token)
        if claims is None:
            return None

        return self._claims_to_context(claims)

    def _decode_token(self, token: str) -> dict[str, Any] | None:
        """Decode and validate JWT. Returns claims dict or None on failure."""
        try:
            import jwt  # PyJWT
        except ImportError:
            logger.warning("jwt_library_unavailable", hint="pip install PyJWT")
            return None

        try:
            options: dict[str, Any] = {}
            kwargs: dict[str, Any] = {
                "algorithms": self._algorithms,
                "options": options,
            }
            if self._audience:
                kwargs["audience"] = self._audience
            if self._issuer:
                kwargs["issuer"] = self._issuer

            claims = jwt.decode(token, self._secret_or_key, **kwargs)
            return claims
        except jwt.ExpiredSignatureError:
            logger.debug("jwt_expired")
        except jwt.InvalidAudienceError:
            logger.debug("jwt_invalid_audience")
        except jwt.InvalidIssuerError:
            logger.debug("jwt_invalid_issuer")
        except jwt.InvalidTokenError as e:
            logger.debug("jwt_invalid_token", error=str(e))
        return None

    def _claims_to_context(self, claims: dict[str, Any]) -> IdentityContext | None:
        """Convert validated JWT claims to an IdentityContext."""
        user_id = claims.get(self._user_id_claim)
        agent_id = claims.get(self._agent_id_claim)
        session_id = claims.get(self._session_id_claim)
        principal_type = claims.get(self._principal_type_claim, "user")
        correlation_id = claims.get(self._correlation_id_claim)

        if not user_id:
            logger.debug("jwt_missing_sub", claim=self._user_id_claim)
            return None

        if principal_type not in ("user", "service", "anonymous"):
            principal_type = "user"

        try:
            caller = CallerIdentity(
                user_id=str(user_id),
                agent_id=str(agent_id) if agent_id else None,
                session_id=str(session_id) if session_id else None,
                principal_type=principal_type,  # type: ignore[arg-type]
            )
        except ValueError as e:
            logger.warning("jwt_identity_construction_failed", error=str(e))
            return None

        return IdentityContext(
            caller=caller,
            correlation_id=str(correlation_id) if correlation_id else None,
        )

    @staticmethod
    def _normalize_headers(
        metadata: list[tuple[str, str]] | Dict[str, str],
    ) -> dict[str, str]:
        """Normalize metadata to lowercase-keyed dict."""
        if isinstance(metadata, dict):
            return {k.lower(): v for k, v in metadata.items()}
        return {k.lower(): v for k, v in metadata}

