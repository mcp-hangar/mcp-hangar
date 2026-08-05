"""An SSRF rule that refuses the feature is not a security win.

#767 routed discovery through `CreateMcpServerCommand`, which was right -- the
old path skipped the duplicate guard, the SSRF check and the registration event.
But the SSRF check exists for endpoints a *human* supplies, and a container or
pod address is private by definition, so every discovered container was refused
(#771). The guard that closed a real hole closed the feature with it.

The test that was missing is the one below that asserts a container **registers**.
The #767 suite asserted that `169.254.169.254` is refused -- true, and it stayed
true while the feature was dead.

Relaxing by provenance alone would only move the hole: a container that labels
itself with a neighbour's address would launder its way there. So provenance is
typed rather than a string, it is set by the construction path rather than
accepted from input, and it grants the specific addresses the runtime reported
rather than an address class.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_hangar.domain.security.ssrf import SsrfBlocked, validate_no_ssrf
from mcp_hangar.domain.value_objects.provenance import Provenance


def _resolves_to(*addresses: str):
    """Patch name resolution to return exactly these addresses."""
    return patch(
        "mcp_hangar.domain.security.ssrf.socket.getaddrinfo",
        return_value=[(None, None, None, None, (a, 0)) for a in addresses],
    )


class TestADiscoveredContainerRegisters:
    """The positive path. Its absence is why #771 shipped."""

    def test_an_rfc1918_container_is_accepted(self) -> None:
        with _resolves_to("10.88.0.7"):
            validate_no_ssrf(
                "http://10.88.0.7:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.88.0.7"}),
            )

    def test_a_published_loopback_binding_is_accepted(self) -> None:
        # The docker source prefers a container's published host-port binding,
        # which is `127.0.0.1:<port>` for the documented topology (Hangar on the
        # host). A blanket "discovery may not use loopback" rule would refuse the
        # most common docker deployment there is.
        with _resolves_to("127.0.0.1"):
            validate_no_ssrf(
                "http://127.0.0.1:18080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"127.0.0.1"}),
            )

    def test_a_cgnat_pod_address_is_accepted(self) -> None:
        # 100.64.0.0/10 is a common Kubernetes pod and service range. An
        # allowlist of "the private ranges we thought of" would reproduce #771
        # on such a cluster, which is why the discovery rule is a denylist.
        with _resolves_to("100.64.3.9"):
            validate_no_ssrf(
                "http://100.64.3.9:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"100.64.3.9"}),
            )

    def test_an_ipv4_mapped_form_is_the_same_host(self) -> None:
        with _resolves_to("::ffff:10.88.0.7"):
            validate_no_ssrf(
                "http://container.local:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.88.0.7"}),
            )


class TestProvenanceGrantsAnAddressNotAClass:
    def test_an_endpoint_the_runtime_did_not_report_is_refused(self) -> None:
        # The laundering path: a container labels itself with a neighbour's
        # address, or an internal service's, and rides discovery's trust to it.
        with _resolves_to("10.0.5.5"), pytest.raises(SsrfBlocked, match="did not report"):
            validate_no_ssrf(
                "http://10.0.5.5:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.88.0.7"}),
            )

    def test_discovery_without_reported_addresses_gets_the_strict_policy(self) -> None:
        # A source that cannot vouch for an address buys nothing by being a
        # discovery source. Failing closed is the safe direction here.
        with _resolves_to("10.88.0.7"), pytest.raises(SsrfBlocked, match="private address"):
            validate_no_ssrf("http://10.88.0.7:8080", provenance=Provenance.DISCOVERY)

    def test_a_name_resolving_to_two_addresses_needs_both_reported(self) -> None:
        # Registration-time DNS rebinding: one answer is the container, the
        # other is somewhere it should not reach.
        with _resolves_to("10.88.0.7", "10.0.5.5"), pytest.raises(SsrfBlocked, match="did not report"):
            validate_no_ssrf(
                "http://container.local:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.88.0.7"}),
            )


class TestTheFloorAppliesToEveryDoor:
    """Some addresses are refused whatever reported them."""

    @pytest.mark.parametrize("address", ["169.254.169.254", "169.254.0.1", "fe80::1", "0.0.0.0", "::"])
    def test_link_local_and_unspecified_are_refused_for_discovery(self, address: str) -> None:
        with _resolves_to(address), pytest.raises(SsrfBlocked):
            validate_no_ssrf(
                f"http://[{address}]" if ":" in address else f"http://{address}",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({address}),
            )

    def test_even_a_runtime_reporting_the_metadata_address_does_not_open_it(self) -> None:
        # The whole point of the class floor: the runtime's word is not enough
        # for the one address an SSRF is usually after.
        with _resolves_to("169.254.169.254"), pytest.raises(SsrfBlocked, match="link-local"):
            validate_no_ssrf(
                "http://169.254.169.254/latest/meta-data/",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"169.254.169.254"}),
            )

    @pytest.mark.parametrize(
        "hostname", ["metadata.google.internal", "instance-data.internal", "ip-10-0-0-1.compute.internal"]
    )
    def test_metadata_hostnames_are_refused_before_resolution(self, hostname: str) -> None:
        # Refused on the name, so a resolver answering with a public address --
        # the classic bypass -- does not help.
        with _resolves_to("93.184.216.34"), pytest.raises(SsrfBlocked, match="metadata hostname"):
            validate_no_ssrf(f"http://{hostname}/", provenance=Provenance.DISCOVERY)


class TestTheHumanPolicyIsUnchanged:
    """What #767 closed stays closed."""

    @pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.1", "172.16.0.1", "127.0.0.1", "::1"])
    def test_private_addresses_stay_refused(self, address: str) -> None:
        with _resolves_to(address), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://internal.example", provenance=Provenance.HUMAN)

    def test_runtime_addresses_do_not_help_a_human_caller(self) -> None:
        # Passing the argument must not be a way to relax the human rule; only
        # the provenance the construction path establishes can do that.
        with _resolves_to("10.88.0.7"), pytest.raises(SsrfBlocked, match="private address"):
            validate_no_ssrf(
                "http://10.88.0.7:8080",
                provenance=Provenance.HUMAN,
                runtime_addresses=frozenset({"10.88.0.7"}),
            )

    def test_the_default_is_the_strict_policy(self) -> None:
        # A call site that says nothing gets the human rules. A new caller
        # cannot relax a security check by forgetting an argument.
        with _resolves_to("10.0.0.1"), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://internal.example")

    def test_a_public_address_is_still_fine(self) -> None:
        with _resolves_to("93.184.216.34"):
            validate_no_ssrf("http://example.com")
