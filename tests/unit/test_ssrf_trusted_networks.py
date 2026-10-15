"""`ssrf.trusted_networks`: the deployment's one relaxation of the strict SSRF policy (#1752)."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.security import ssrf
from mcp_hangar.domain.security.ssrf import (
    SsrfBlocked,
    parse_trusted_networks,
    set_trusted_networks,
    trusted_networks,
    validate_no_ssrf,
)
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.http_client import _SsrfGuardedTransport
from mcp_hangar.server.config import apply_process_config, check_process_config
from mcp_hangar.server.config_schema import validate_config

SERVICE_RANGE = "10.96.0.0/12"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    set_trusted_networks(())
    yield
    set_trusted_networks(())


def _resolving(*ips: str):
    def _stub(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    return patch.object(ssrf.socket, "getaddrinfo", _stub)


def _trust(*cidrs: str) -> None:
    set_trusted_networks(parse_trusted_networks(list(cidrs)))


class TestParse:
    def test_absent_trusts_nothing(self):
        assert parse_trusted_networks(None) == ()

    def test_networks_are_parsed(self):
        assert parse_trusted_networks([SERVICE_RANGE, "fd00:10:96::/112"]) == (
            ipaddress.ip_network(SERVICE_RANGE),
            ipaddress.ip_network("fd00:10:96::/112"),
        )

    @pytest.mark.parametrize(
        "value",
        [
            "10.96.0.0/12",  # a string, not a list
            [12],
            ["not-a-cidr"],
            ["10.96.0.1/12"],  # host bits set: probably not the network meant
            ["0.0.0.0/0"],
            ["::/0"],
            ["127.0.0.0/8"],
            ["127.0.0.1/32"],
            ["::1/128"],
            ["169.254.169.254/32"],
            ["169.254.0.0/16"],
            ["fe80::/10"],
            ["0.0.0.0/8"],
            ["64.0.0.0/2"],  # covers 127/8
        ],
    )
    def test_refused(self, value):
        with pytest.raises(ValueError, match="ssrf.trusted_networks"):
            parse_trusted_networks(value)


class TestPolicy:
    def test_strict_without_trust(self):
        with _resolving("10.96.0.10"), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://notes.providers.svc.cluster.local:8080/mcp")

    def test_a_trusted_address_passes(self):
        _trust(SERVICE_RANGE)
        with _resolving("10.96.0.10"):
            validate_no_ssrf("http://notes.providers.svc.cluster.local:8080/mcp")

    def test_the_ipv4_mapped_form_of_a_trusted_address_passes(self):
        _trust(SERVICE_RANGE)
        with _resolving("::ffff:10.96.0.10"):
            validate_no_ssrf("http://notes.providers.svc.cluster.local:8080/mcp")

    def test_a_private_address_outside_the_networks_is_refused(self):
        _trust(SERVICE_RANGE)
        with _resolving("10.0.0.5"), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://elsewhere.example:8080/mcp")

    def test_one_untrusted_address_among_trusted_ones_is_refused(self):
        _trust(SERVICE_RANGE)
        with _resolving("10.96.0.10", "192.168.1.9"), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://split.example:8080/mcp")

    def test_metadata_hostnames_and_link_local_stay_refused(self):
        _trust(SERVICE_RANGE)
        with _resolving("10.96.0.10"), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://metadata.google.internal/")
        with _resolving("169.254.169.254"), pytest.raises(SsrfBlocked):
            validate_no_ssrf("http://looks-fine.example/")

    def test_discovery_stays_scoped_to_the_runtime_addresses(self):
        """Trust widens the HUMAN rule only; a discovered endpoint still needs its own address."""
        _trust(SERVICE_RANGE)
        with _resolving("10.96.0.10"), pytest.raises(SsrfBlocked):
            validate_no_ssrf(
                "http://pod.example:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.244.1.7"}),
            )


class TestConnectTime:
    def _send(self) -> httpx.Request:
        transport = _SsrfGuardedTransport(enforce_ssrf=True, provenance=Provenance.HUMAN, runtime_addresses=None)
        request = httpx.Request("POST", "http://notes.providers.svc.cluster.local:8080/mcp", json={})
        with _resolving("10.96.0.10"):
            with patch.object(httpx.HTTPTransport, "handle_request", return_value=httpx.Response(200)) as sup:
                transport.handle_request(request)
        return sup.call_args.args[0]

    def test_a_trusted_address_is_pinned(self):
        _trust(SERVICE_RANGE)
        sent = self._send()
        assert sent.url.host == "10.96.0.10"
        assert sent.headers["Host"] == "notes.providers.svc.cluster.local:8080"

    def test_removing_the_trust_refuses_the_next_connection(self):
        _trust(SERVICE_RANGE)
        self._send()
        set_trusted_networks(())
        with pytest.raises(httpx.ConnectError):
            self._send()


class TestConfig:
    def test_the_section_is_applied_and_an_absent_one_clears_it(self):
        apply_process_config({"ssrf": {"trusted_networks": [SERVICE_RANGE]}})
        assert trusted_networks() == (ipaddress.ip_network(SERVICE_RANGE),)
        apply_process_config({})
        assert trusted_networks() == ()

    @pytest.mark.parametrize("section", [["10.0.0.0/8"], {"trusted_networks": ["127.0.0.0/8"]}])
    def test_a_bad_section_refuses_the_config_and_applies_nothing(self, section):
        _trust(SERVICE_RANGE)
        with pytest.raises(ConfigurationError):
            check_process_config({"ssrf": section})
        with pytest.raises(ConfigurationError):
            apply_process_config({"ssrf": section})
        assert trusted_networks() == (ipaddress.ip_network(SERVICE_RANGE),)

    def test_the_schema_knows_the_key(self):
        assert validate_config({"ssrf": {"trusted_networks": [SERVICE_RANGE]}}) == []
