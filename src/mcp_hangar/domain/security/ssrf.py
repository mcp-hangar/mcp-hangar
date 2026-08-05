"""SSRF validation.

One check, two provenances, and the asymmetry between them is the whole design.

A human-supplied endpoint is untrusted: every private range is refused, because
"connect to 127.0.0.1:6379" is the attack, not a deployment. An endpoint a
discovery source read off the container runtime is a different object -- a
container or pod address is private *by definition*, so applying the human rule
to it refuses the feature rather than an attack. That is exactly what happened
after #767: every discovered container was rejected (#771).

Relaxing by provenance alone would be a laundering path -- a container labels
itself with a neighbouring pod's address and rides discovery's trust to it. So
discovery is not granted an address *class*; it is granted the specific
addresses the runtime reported for that container, and nothing else. The class
denylist below still applies on top, so link-local and the cloud metadata
endpoint stay refused through every door.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from ..value_objects.provenance import Provenance


#: Refused for a human-supplied endpoint: every private range.
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

#: Refused whatever the provenance, even when a runtime reports it. Link-local
#: carries the cloud metadata endpoint (169.254.169.254), and the unspecified
#: address is not somewhere anything is served from. Deliberately NOT a list of
#: private ranges: pod CIDRs are not guaranteed to be RFC1918 -- 100.64.0.0/10
#: is a common Kubernetes pod and service range -- so an allowlist of "the
#: private ranges we thought of" would reproduce #771 on such a cluster.
_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
)

#: Hostnames that reach a cloud metadata service without ever looking private.
_METADATA_SUFFIXES = (".internal", ".metadata.google.internal", ".compute.internal")


class SsrfBlocked(ValueError):
    """An endpoint was refused. Subclasses ValueError, which callers catch."""


def _resolved_addresses(hostname: str) -> list[str]:
    try:
        addr_infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
    except OSError:
        return []
    return [str(info[4][0]) for info in addr_infos]


def _in_any(ip_str: str, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def _normalize(address: str) -> str:
    """`::ffff:10.0.0.1` and `10.0.0.1` are the same host; compare them as one."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped or ip)


def validate_no_ssrf(
    url: str,
    *,
    provenance: Provenance = Provenance.HUMAN,
    runtime_addresses: frozenset[str] | None = None,
) -> None:
    """Refuse an endpoint the caller must not be allowed to reach.

    Args:
        url: The endpoint being registered.
        provenance: How the registration arrived. Defaults to HUMAN, so a caller
            that says nothing gets the strict policy -- a new call site cannot
            relax the check by forgetting an argument.
        runtime_addresses: For DISCOVERY, the addresses the container runtime
            reported for this container or pod. Every address the endpoint
            resolves to must be one of them. Absent or empty, DISCOVERY is
            treated as HUMAN: provenance on its own grants nothing.

    Raises:
        SsrfBlocked: if the endpoint is refused.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return

    lowered = hostname.lower()
    if lowered.endswith(_METADATA_SUFFIXES):
        raise SsrfBlocked(f"SSRF blocked: {hostname} is a cloud metadata hostname")

    addresses = _resolved_addresses(hostname)
    if not addresses:
        # Unresolvable. Unchanged from the original behaviour: nothing to judge,
        # and a name that does not resolve cannot be connected to either.
        return

    for address in addresses:
        if _in_any(address, _ALWAYS_BLOCKED_NETWORKS):
            raise SsrfBlocked(f"SSRF blocked: {address} is link-local, unspecified or metadata-adjacent")

    scoped = provenance is Provenance.DISCOVERY and bool(runtime_addresses)
    if not scoped:
        for address in addresses:
            if _in_any(address, _BLOCKED_NETWORKS):
                raise SsrfBlocked("SSRF blocked: endpoint resolves to private address")
        return

    # DISCOVERY with a runtime-reported address set. The endpoint is allowed to
    # be private -- that is the normal case -- but only where the runtime put it.
    # This is also what closes DNS rebinding at registration time: a name that
    # resolves to two addresses passes only if the runtime reported both.
    reported = {_normalize(a) for a in (runtime_addresses or frozenset())}
    for address in addresses:
        if _normalize(address) not in reported:
            raise SsrfBlocked(
                f"SSRF blocked: discovered endpoint resolves to {address}, "
                f"which the container runtime did not report for it"
            )
