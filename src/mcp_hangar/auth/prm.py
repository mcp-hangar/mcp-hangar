"""RFC 9728 Protected Resource Metadata helpers.

Provides utilities for building the PRM endpoint response and the
WWW-Authenticate header that advertises the PRM URL on 401 responses.

This module is intentionally thin: it only ADVERTISES the resource server
(issuer URL, resource URI). It does NOT issue tokens, perform DCR, or touch
any token-validation logic. Hangar remains a pure Resource Server.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

_PRM_PATH = "/.well-known/oauth-protected-resource"


def build_resource_base_url(scope: MutableMapping[str, Any]) -> str:
    """Derive the base URL (scheme + host) from an ASGI scope.

    Used as a fallback when no configured resource_uri is available.
    Note: proxies can make the Host header unreliable; prefer a configured
    resource_uri (auth.oidc.resource_uri) over this derived value.
    """
    headers: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        headers[key.decode("latin-1").lower()] = value.decode("latin-1")

    host = headers.get("host", "localhost")
    # Determine scheme from forwarded headers or ASGI scope hint.
    scheme = headers.get("x-forwarded-proto", "")
    if not scheme:
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


def build_prm_response(issuer: str, resource_uri: str) -> dict:
    """Build the PRM JSON body (RFC 9728 §3).

    Args:
        issuer: OIDC issuer URL from auth.oidc.issuer.
        resource_uri: Absolute URI identifying this resource server.

    Returns:
        Dict suitable for JSON serialisation.
    """
    return {
        "resource": resource_uri,
        "authorization_servers": [issuer],
    }
