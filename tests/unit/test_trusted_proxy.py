"""Tests for trusted proxy resolution."""

from unittest.mock import Mock

from mcp_hangar.infrastructure.identity.header_extractor import HeaderIdentityExtractor
from mcp_hangar.infrastructure.identity.trusted_proxy import TrustedProxyResolver


def test_trusted_proxy_resolver_matches_single_ip() -> None:
    resolver = TrustedProxyResolver(frozenset({"203.0.113.10"}))

    assert resolver.is_trusted("203.0.113.10") is True
    assert resolver.is_trusted("203.0.113.11") is False


def test_trusted_proxy_resolver_matches_cidr_ipv4_and_ipv6() -> None:
    resolver = TrustedProxyResolver(frozenset({"10.0.0.0/8", "2001:db8::/32"}))

    assert resolver.is_trusted("10.2.3.4") is True
    assert resolver.is_trusted("2001:db8::1") is True
    assert resolver.is_trusted("192.168.1.1") is False


def test_trusted_proxy_resolver_empty_configuration_logs_warning(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr("mcp_hangar.infrastructure.identity.trusted_proxy.logger", logger)

    resolver = TrustedProxyResolver(frozenset())

    assert resolver.proxies == frozenset()
    logger.warning.assert_called_once_with("trusted_proxies_empty")


def test_trusted_proxy_resolver_loads_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TRUSTED_PROXIES", "10.0.0.0/8, 203.0.113.10, 2001:db8::/32")

    resolver = TrustedProxyResolver()

    assert resolver.proxies == frozenset({"10.0.0.0/8", "203.0.113.10", "2001:db8::/32"})
    assert resolver.is_trusted("10.9.8.7") is True
    assert resolver.is_trusted("203.0.113.10") is True
    assert resolver.is_trusted("2001:db8::42") is True


def test_trusted_proxy_resolver_invalid_source_ip_returns_false_and_logs(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr("mcp_hangar.infrastructure.identity.trusted_proxy.logger", logger)

    resolver = TrustedProxyResolver(frozenset({"127.0.0.1"}))

    assert resolver.is_trusted("not-an-ip") is False
    logger.warning.assert_called_with("trusted_proxy_source_ip_invalid", source_ip="not-an-ip")


def test_header_identity_extractor_warns_when_no_trusted_proxies(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr("mcp_hangar.infrastructure.identity.header_extractor.logger", logger)

    HeaderIdentityExtractor()

    logger.warning.assert_called_once_with("header_identity_no_trusted_proxies")


def test_header_identity_extractor_requires_source_ip_when_resolver_present(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr("mcp_hangar.infrastructure.identity.header_extractor.logger", logger)

    extractor = HeaderIdentityExtractor(trusted_proxies=TrustedProxyResolver(frozenset({"10.0.0.0/8"})))

    assert extractor.extract({"x-user-id": "alice"}) is None
    logger.warning.assert_called_once_with("header_identity_source_ip_missing")
