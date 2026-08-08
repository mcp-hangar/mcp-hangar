"""Connect-time SSRF enforcement / DNS-rebinding defence.

`validate_no_ssrf` runs once, at registration. httpx then re-resolves the
hostname itself on every connect with no second check, so a name that resolved
public at registration can be re-pointed at an internal address and every later
tool call follows it. `_SsrfGuardedTransport` closes that gap: it re-runs the
same policy on every request and pins the connection to a validated IP while
keeping the original name for the Host header and TLS verification.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from mcp_hangar.domain.security.ssrf import SsrfBlocked, resolve_validated_addresses
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.http_client import _SsrfGuardedTransport


def _getaddrinfo_returning(*ips: str):
    """A socket.getaddrinfo stub resolving a host to the given IP strings."""

    def _stub(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    return _stub


# ---------------------------------------------------------------------------
# The shared policy core, exposed for the transport.
# ---------------------------------------------------------------------------


class TestResolveValidatedAddresses:
    def test_human_public_host_returns_the_addresses(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning("93.184.216.34")):
            addrs = resolve_validated_addresses("https://mcp.example.com", provenance=Provenance.HUMAN)
        assert addrs == ["93.184.216.34"]

    @pytest.mark.parametrize("private_ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.9"])
    def test_human_private_host_is_refused(self, private_ip):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning(private_ip)):
            with pytest.raises(SsrfBlocked):
                resolve_validated_addresses("https://rebound.example.com", provenance=Provenance.HUMAN)

    def test_discovery_allows_a_runtime_reported_private_ip(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning("10.1.2.3")):
            addrs = resolve_validated_addresses(
                "http://svc.pod:8080",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.1.2.3"}),
            )
        assert addrs == ["10.1.2.3"]

    def test_discovery_refuses_a_private_ip_the_runtime_did_not_report(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning("10.9.9.9")):
            with pytest.raises(SsrfBlocked):
                resolve_validated_addresses(
                    "http://svc.pod:8080",
                    provenance=Provenance.DISCOVERY,
                    runtime_addresses=frozenset({"10.1.2.3"}),
                )


# ---------------------------------------------------------------------------
# The transport: pins to a validated IP, or refuses the connection.
# ---------------------------------------------------------------------------


def _make_request() -> httpx.Request:
    return httpx.Request("POST", "https://mcp.example.com/rpc", json={"jsonrpc": "2.0"})


class TestSsrfGuardedTransport:
    def test_human_public_host_connects_pinned_to_the_ip(self):
        transport = _SsrfGuardedTransport(provenance=Provenance.HUMAN, runtime_addresses=None)
        request = _make_request()
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning("93.184.216.34")):
            with patch.object(httpx.HTTPTransport, "handle_request", return_value=httpx.Response(200)) as sup:
                transport.handle_request(request)
        sup.assert_called_once()
        sent = sup.call_args.args[0]
        # Pinned to the validated IP, but the name is preserved for vhost + TLS.
        assert sent.url.host == "93.184.216.34"
        assert sent.headers["Host"] == "mcp.example.com"
        assert sent.extensions["sni_hostname"] == "mcp.example.com"

    @pytest.mark.parametrize("rebound_ip", ["169.254.169.254", "10.0.0.5", "127.0.0.1"])
    def test_rebinding_to_an_internal_ip_is_refused_at_connect(self, rebound_ip):
        transport = _SsrfGuardedTransport(provenance=Provenance.HUMAN, runtime_addresses=None)
        request = _make_request()
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning(rebound_ip)):
            with patch.object(httpx.HTTPTransport, "handle_request") as sup:
                with pytest.raises(httpx.ConnectError):
                    transport.handle_request(request)
        sup.assert_not_called()  # never reaches the network

    def test_discovery_connects_to_its_runtime_reported_private_ip(self):
        transport = _SsrfGuardedTransport(
            provenance=Provenance.DISCOVERY, runtime_addresses=frozenset({"10.1.2.3"})
        )
        request = httpx.Request("POST", "http://svc.pod:8080/rpc", json={"jsonrpc": "2.0"})
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", _getaddrinfo_returning("10.1.2.3")):
            with patch.object(httpx.HTTPTransport, "handle_request", return_value=httpx.Response(200)) as sup:
                transport.handle_request(request)
        sup.assert_called_once()
        sent = sup.call_args.args[0]
        assert sent.url.host == "10.1.2.3"
        assert sent.headers["Host"] == "svc.pod:8080"
