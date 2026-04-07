"""Header-based identity extractor.

Extracts caller identity from well-known HTTP/gRPC headers.
This is the simplest extractor — no token validation, just header passthrough.
Suitable for trusted environments where a gateway has already validated identity.
"""

from __future__ import annotations


import structlog

from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

logger = structlog.get_logger(__name__)

# Well-known header names (lowercase for case-insensitive matching)
HEADER_USER_ID = "x-user-id"
HEADER_AGENT_ID = "x-agent-id"
HEADER_SESSION_ID = "x-session-id"
HEADER_PRINCIPAL_TYPE = "x-principal-type"
HEADER_CORRELATION_ID = "x-correlation-id"


class HeaderIdentityExtractor:
    """Extracts identity from request headers.

    This extractor reads identity from pre-validated headers, typically
    set by an upstream gateway, sidecar proxy, or authentication middleware.

    It implements `IIdentityExtractor` protocol.

    Header mapping:
        x-user-id         -> CallerIdentity.user_id
        x-agent-id        -> CallerIdentity.agent_id
        x-session-id      -> CallerIdentity.session_id
        x-principal-type   -> CallerIdentity.principal_type
        x-correlation-id   -> IdentityContext.correlation_id
    """

    def __init__(
        self,
        *,
        user_id_header: str = HEADER_USER_ID,
        agent_id_header: str = HEADER_AGENT_ID,
        session_id_header: str = HEADER_SESSION_ID,
        principal_type_header: str = HEADER_PRINCIPAL_TYPE,
        correlation_id_header: str = HEADER_CORRELATION_ID,
    ) -> None:
        self._user_id_header = user_id_header.lower()
        self._agent_id_header = agent_id_header.lower()
        self._session_id_header = session_id_header.lower()
        self._principal_type_header = principal_type_header.lower()
        self._correlation_id_header = correlation_id_header.lower()

    def extract(
        self,
        metadata: list[tuple[str, str]] | dict[str, str] | None,
    ) -> IdentityContext | None:
        """Extract identity context from header metadata.

        Args:
            metadata: Headers as list of tuples (gRPC-style) or dict (HTTP-style).
                      Returns None if metadata is empty or no identity headers found.

        Returns:
            IdentityContext if any identity header is present, None otherwise.
        """
        if metadata is None:
            return None

        headers = self._normalize_headers(metadata)

        user_id = headers.get(self._user_id_header)
        agent_id = headers.get(self._agent_id_header)
        session_id = headers.get(self._session_id_header)
        principal_type = headers.get(self._principal_type_header, "anonymous")
        correlation_id = headers.get(self._correlation_id_header)

        # If no identity headers present at all, return None
        if not user_id and not agent_id and not session_id:
            return None

        # Validate principal_type
        if principal_type not in ("user", "service", "anonymous"):
            logger.warning(
                "invalid_principal_type",
                value=principal_type,
                fallback="anonymous",
            )
            principal_type = "anonymous"

        try:
            caller = CallerIdentity(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                principal_type=principal_type,  # type: ignore[arg-type]
            )
        except ValueError as e:
            logger.warning("identity_extraction_failed", error=str(e))
            return None

        return IdentityContext(caller=caller, correlation_id=correlation_id)

    @staticmethod
    def _normalize_headers(
        metadata: list[tuple[str, str]] | dict[str, str],
    ) -> dict[str, str]:
        """Normalize metadata to a lowercase-keyed dict."""
        if isinstance(metadata, dict):
            return {k.lower(): v for k, v in metadata.items()}
        # list of tuples (gRPC metadata style)
        return {k.lower(): v for k, v in metadata}
