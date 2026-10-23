"""The strict SSRF policy refuses IPv6 unique local and RFC 6598 shared addresses (#1754).

Both are where a cluster's own pods and Services commonly live without being
RFC 1918: `fc00::/7` on dual-stack and IPv6 clusters, `100.64.0.0/10` on many
distributions. Under HUMAN provenance they are private like 10/8, at
registration and at connect time, unless the deployment trusts them.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from mcp_hangar.domain.security import ssrf
from mcp_hangar.domain.security.ssrf import (
    SsrfBlocked,
    endpoint_is_a_literal_the_strict_policy_refuses,
    parse_trusted_networks,
    resolve_validated_addresses,
    set_trusted_networks,
    validate_no_ssrf,
)
from mcp_hangar.domain.value_objects.provenance import Provenance

pytestmark = pytest.mark.security

REFUSED = ["fd00::1", "fc00::1", "fdff:ffff::1", "100.64.0.1", "100.127.255.254", "::ffff:100.64.0.1"]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    set_trusted_networks(())
    yield
    set_trusted_networks(())


def _resolving(*ips: str):
    def _stub(host, port, *args, **kwargs):
        return [(10 if ":" in ip else 2, 1, 6, "", (ip, 0)) for ip in ips]

    return patch.object(ssrf.socket, "getaddrinfo", _stub)


@pytest.mark.parametrize("address", REFUSED)
def test_registration_refuses_the_range_for_a_human_endpoint(address: str) -> None:
    with _resolving(address), pytest.raises(SsrfBlocked):
        validate_no_ssrf("https://upstream.example:8443/mcp")


@pytest.mark.parametrize("address", REFUSED)
def test_the_connect_time_check_refuses_it_too(address: str) -> None:
    with _resolving(address), pytest.raises(SsrfBlocked):
        resolve_validated_addresses("https://upstream.example:8443/mcp")


def test_a_literal_in_the_range_is_one_the_strict_policy_refuses() -> None:
    assert endpoint_is_a_literal_the_strict_policy_refuses("https://[fd00::1]:8080")
    assert endpoint_is_a_literal_the_strict_policy_refuses("http://100.64.0.10:8080/mcp")


@pytest.mark.parametrize("address", ["fbff:ffff::1", "fe00::1", "100.63.255.255", "100.128.0.1"])
def test_the_neighbouring_public_addresses_still_pass(address: str) -> None:
    with _resolving(address):
        assert resolve_validated_addresses("https://upstream.example/mcp") == [address]


def test_a_trusted_network_lets_the_deployment_register_in_it() -> None:
    set_trusted_networks(parse_trusted_networks(["fd00:10:96::/112", "100.64.0.0/10"]))
    with _resolving("fd00:10:96::a"):
        assert resolve_validated_addresses("https://svc.example/mcp") == ["fd00:10:96::a"]
    with _resolving("100.64.3.4"):
        validate_no_ssrf("https://svc.example/mcp")
    with _resolving("fd00:99::a"), pytest.raises(SsrfBlocked):
        validate_no_ssrf("https://other.example/mcp")


def test_discovery_still_reaches_the_address_its_runtime_reported() -> None:
    with _resolving("fd00::5"):
        assert resolve_validated_addresses(
            "http://[fd00::5]:8080/mcp", provenance=Provenance.DISCOVERY, runtime_addresses=frozenset({"fd00::5"})
        ) == ["fd00::5"]
