"""Unit tests for SSRF endpoint validation."""

from unittest.mock import patch

import pytest

from mcp_hangar.domain.security.ssrf import validate_no_ssrf


class TestSSRFValidation:
    def test_private_ipv4_blocked(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(None, None, None, None, ("10.0.0.1", 0))]
            with pytest.raises(ValueError, match="SSRF blocked: endpoint resolves to private address"):
                validate_no_ssrf("http://internal.example")

    def test_localhost_blocked(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
            with pytest.raises(ValueError, match="SSRF blocked: endpoint resolves to private address"):
                validate_no_ssrf("http://localhost")

    def test_ipv6_loopback_blocked(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(None, None, None, None, ("::1", 0, 0, 0))]
            with pytest.raises(ValueError, match="SSRF blocked: endpoint resolves to private address"):
                validate_no_ssrf("http://[::1]")

    def test_public_ip_allowed(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
            validate_no_ssrf("http://example.com")

    def test_dns_failure_allowed(self):
        with patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", side_effect=OSError):
            validate_no_ssrf("http://unresolvable.example")
