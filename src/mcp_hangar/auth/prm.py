"""RFC 9728 Protected Resource Metadata helpers.

Provides utilities for building the PRM endpoint response and the
WWW-Authenticate header that advertises the PRM URL on 401 responses.

This module is intentionally thin: it only ADVERTISES the resource server
(issuer URL, resource URI). It does NOT issue tokens, perform DCR, or touch
any token-validation logic. Hangar remains a pure Resource Server.
"""

from __future__ import annotations

from collections.abc import MutableMapping

from mcp_hangar.logging_config import get_logger
from mcp_hangar.trusted_hosts import fallback_host, host_is_trusted
from typing import Any

_PRM_PATH = "/.well-known/oauth-protected-resource"


logger = get_logger(__name__)


def build_resource_base_url(scope: MutableMapping[str, Any]) -> str:
    """Derive the base URL (scheme + host) from an ASGI scope.

    Used as a fallback when no configured resource_uri is available. Prefer
    setting `auth.oidc.resource_uri`, which skips this entirely.

    The Host header is attacker-controlled, and the two places that call this
    are reached BEFORE any host check: `AuthMiddlewareHTTP` builds the RFC 9728
    `WWW-Authenticate` challenge from outside `TrustedHostMiddleware`, and the
    `.well-known` PRM endpoint on the serving app has no such middleware at all.
    A forged Host would therefore be echoed back as this resource's identity, in
    the document clients use to decide where to send tokens.

    So an untrusted Host is ignored rather than reflected: the advertised
    identity falls back to the first configured trusted host. That degrades to a
    value the operator chose, which is the safe direction -- a client that
    cannot reach it fails to authenticate, rather than authenticating against
    somewhere an attacker named.
    """
    headers: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        headers[key.decode("latin-1").lower()] = value.decode("latin-1")

    host = headers.get("host", "localhost")
    if not host_is_trusted(host):
        logger.warning("prm_untrusted_host_ignored", host=host)
        host = fallback_host()
    # Determine scheme from forwarded headers or ASGI scope hint.
    scheme = headers.get("x-forwarded-proto", "")
    if scheme not in ("http", "https"):
        scheme = scope.get("scheme", "http")
    return f"{scheme}://{host}"


def prm_url(resource_base: str) -> str:
    """Return the absolute PRM URL for a given resource base URL."""
    return resource_base.rstrip("/") + _PRM_PATH


def build_www_authenticate(resource_base: str) -> str:
    """Build the WWW-Authenticate header value for a 401 response.

    Format (RFC 9728 §4 + RFC 6750):
        Bearer resource_metadata="<prm_url>", ApiKey
    """
    return f'Bearer resource_metadata="{prm_url(resource_base)}", ApiKey'


def build_prm_response(issuers: list[str], resource_uri: str) -> dict:
    """Build the PRM JSON body (RFC 9728 §3).

    Args:
        issuers: All trusted OIDC issuer URLs from auth.oidc. Each is advertised
            as an authorization server so clients can discover every issuer this
            resource server accepts tokens from.
        resource_uri: Absolute URI identifying this resource server.

    Returns:
        Dict suitable for JSON serialisation.
    """
    return {
        "resource": resource_uri,
        "authorization_servers": list(issuers),
    }
