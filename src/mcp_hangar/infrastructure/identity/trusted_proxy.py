"""Trusted proxy resolution for forwarded request metadata."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1"})
TRUSTED_PROXIES_ENV_VAR = "MCP_TRUSTED_PROXIES"


class TrustedProxyResolver:
    """Resolves whether a source IP belongs to a trusted proxy."""

    _proxies: frozenset[str]
    _networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]

    def __init__(self, proxies: frozenset[str] | None = None) -> None:
        configured_proxies = self._load_proxies(proxies)
        valid_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        valid_proxies: set[str] = set()

        for proxy in configured_proxies:
            try:
                network = ipaddress.ip_network(proxy, strict=False)
            except ValueError:
                logger.warning("trusted_proxy_invalid", proxy=proxy)
                continue

            valid_networks.append(network)
            valid_proxies.add(proxy)

        self._proxies = frozenset(valid_proxies)
        self._networks = tuple(valid_networks)

        if not self._proxies:
            logger.warning("trusted_proxies_empty")

    @property
    def proxies(self) -> frozenset[str]:
        """Return the normalized trusted proxy configuration."""
        return self._proxies

    def is_trusted(self, ip: str) -> bool:
        """Return whether the given IP belongs to a trusted proxy."""
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            logger.warning("trusted_proxy_source_ip_invalid", source_ip=ip)
            return False

        return any(address in network for network in self._networks)

    @staticmethod
    def _load_proxies(proxies: frozenset[str] | None) -> frozenset[str]:
        if proxies is not None:
            return frozenset(proxy.strip() for proxy in proxies if proxy.strip())

        env_value = os.getenv(TRUSTED_PROXIES_ENV_VAR)
        if env_value is None:
            return DEFAULT_TRUSTED_PROXIES

        return frozenset(proxy.strip() for proxy in env_value.split(",") if proxy.strip())


def normalize_http_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a lowercase-keyed copy of HTTP headers."""
    return {key.lower(): value for key, value in headers.items()}


def headers_from_asgi_scope(scope_headers: list[tuple[bytes, bytes]] | None) -> dict[str, str]:
    """Decode ASGI scope headers into a lowercase-keyed dict."""
    if not scope_headers:
        return {}

    return {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope_headers}


def resolve_source_ip(
    *,
    headers: Mapping[str, str],
    client_host: str | None,
    trusted_proxies: TrustedProxyResolver | None = None,
    default: str | None = "unknown",
) -> str | None:
    """Resolve the effective request source IP.

    Uses the direct socket peer by default. If the peer is a trusted proxy and
    ``X-Forwarded-For`` is present, returns the first forwarded address.
    """
    source_ip = client_host or default
    normalized_headers = normalize_http_headers(headers)

    if client_host and trusted_proxies and trusted_proxies.is_trusted(client_host):
        forwarded_for = normalized_headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    return source_ip
