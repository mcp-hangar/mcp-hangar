"""SSRF validation helpers."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
)


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(str(ip_str))
    return any(ip in network for network in _BLOCKED_NETWORKS)


def validate_no_ssrf(url: str) -> None:
    """Raise ValueError if the URL resolves to a blocked address."""

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return

    try:
        addr_infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
    except OSError:
        return

    for _, _, _, _, sockaddr in addr_infos:
        ip_str = str(sockaddr[0])
        if _is_blocked_ip(ip_str):
            raise ValueError("SSRF blocked: endpoint resolves to private address")
