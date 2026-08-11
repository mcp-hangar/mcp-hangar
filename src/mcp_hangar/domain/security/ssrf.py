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

This same policy is enforced twice: `validate_no_ssrf` refuses an endpoint when
it is registered, and `resolve_validated_addresses` re-applies it at connect
time (see `http_client._SsrfGuardedTransport`), returning the validated IP the
transport then pins the connection to. Registration-time validation alone would
leave DNS rebinding open -- a name that resolved to a public address when it was
registered, re-pointed at an internal one before the next call -- because httpx
re-resolves the hostname itself on every connect. The connect-time check closes
that on the HUMAN path as well as DISCOVERY.
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


def _normalize(address: str) -> str:
    """`::ffff:10.0.0.1` and `10.0.0.1` are the same host; compare them as one."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped or ip)


def _in_any(ip_str: str, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    # Normalize first: an IPv4-mapped IPv6 address is the same host as its IPv4
    # form, but membership of an IPv6Address in an IPv4Network is always False.
    # Skipping this step accepted ::ffff:169.254.169.254 / ::ffff:127.0.0.1
    # while refusing the unmapped forms (#899).
    try:
        ip = ipaddress.ip_address(_normalize(ip_str))
    except ValueError:
        return False
    return any(ip in network for network in networks)


def _resolve_and_validate(
    url: str,
    *,
    provenance: Provenance,
    runtime_addresses: frozenset[str] | None,
) -> list[str]:
    """The one policy, returning the resolved addresses that passed it.

    Shared core of `validate_no_ssrf` (which discards the return) and
    `resolve_validated_addresses` (which connects to one of them). Every refusal
    path raises `SsrfBlocked`; an empty list means there was nothing to judge --
    no hostname, or a name that does not resolve -- which both callers treat as
    "allowed", because a name that cannot be resolved cannot be connected to.

    Raises:
        SsrfBlocked: if the endpoint is refused.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return []

    lowered = hostname.lower()
    if lowered.endswith(_METADATA_SUFFIXES):
        raise SsrfBlocked(f"SSRF blocked: {hostname} is a cloud metadata hostname")

    addresses = _resolved_addresses(hostname)
    if not addresses:
        # Unresolvable. Unchanged from the original behaviour: nothing to judge,
        # and a name that does not resolve cannot be connected to either.
        return []

    for address in addresses:
        if _in_any(address, _ALWAYS_BLOCKED_NETWORKS):
            raise SsrfBlocked(f"SSRF blocked: {address} is link-local, unspecified or metadata-adjacent")

    scoped = provenance is Provenance.DISCOVERY and bool(runtime_addresses)
    if not scoped:
        for address in addresses:
            if _in_any(address, _BLOCKED_NETWORKS):
                raise SsrfBlocked("SSRF blocked: endpoint resolves to private address")
        return addresses

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
    return addresses


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
    _resolve_and_validate(url, provenance=provenance, runtime_addresses=runtime_addresses)


def endpoint_is_a_literal_the_strict_policy_refuses(url: str) -> bool:
    """Would the strict policy refuse this endpoint on its written address alone?

    Answered without resolving anything, and only for a literal: a name is
    whatever DNS says it is at the moment it is dialled, which is the question
    `resolve_validated_addresses` exists to ask on every request and not one to
    pre-empt from a stored string. A name therefore answers False.

    Exists for one caller -- restoring a server whose record predates
    `enforce_ssrf` -- which has to tell "an endpoint a human registered under
    the strict policy" from "a container address discovery reported", with
    nothing but the row. Kept here, beside the networks it asks about, so the
    two cannot drift: a copy of the list somewhere else is how a decision that
    was safe when written stops being safe.
    """
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        ipaddress.ip_address(_normalize(host))
    except ValueError:
        return False
    return _in_any(_normalize(host), _BLOCKED_NETWORKS)


def resolve_validated_addresses(
    url: str,
    *,
    provenance: Provenance = Provenance.HUMAN,
    runtime_addresses: frozenset[str] | None = None,
) -> list[str]:
    """Resolve `url`'s host and return the addresses that passed the SSRF policy.

    The same check as `validate_no_ssrf`, exposed so the transport can decide
    *which IP to connect to* rather than re-resolving the name independently at
    connect time (the DNS-rebinding gap: a name validated once at registration,
    then re-pointed at 169.254.169.254 / 10.x / 127.0.0.1). The transport calls
    this on every request -- never caching -- and connects to one of the
    returned IPs, so a rebind is refused on every new connection.

    Args:
        url: The endpoint about to be connected to.
        provenance: HUMAN (strict: every private range refused) or DISCOVERY
            (the endpoint may be private, but only at a runtime-reported address).
            Defaults to HUMAN so a forgetful call site gets the strict policy.
        runtime_addresses: For DISCOVERY, the addresses the container runtime
            reported. Absent or empty, DISCOVERY is treated as HUMAN.

    Returns:
        The validated, resolved IP strings. Empty when the host is absent or does
        not resolve -- there is then nothing to pin to, and the caller connects
        by name and fails naturally.

    Raises:
        SsrfBlocked: if any resolved address is refused.
    """
    return _resolve_and_validate(url, provenance=provenance, runtime_addresses=runtime_addresses)
