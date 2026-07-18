"""WebSocket handshake Origin/Host validation at the Hangar edge (#498).

Covers the ``_ws_handshake_allowed`` policy: loopback is trusted; non-loopback
is fail-closed with a browser-scoped Origin check (present must be allow-listed,
missing is allowed) and a Host allowlist check.
"""

from __future__ import annotations

import pytest

from mcp_hangar.fastmcp_server.asgi import _strip_host_port, _ws_handshake_allowed

LOOPBACK = ("127.0.0.1", 8000)
REMOTE = ("192.168.1.5", 8000)


def _scope(server=REMOTE, headers=None) -> dict:
    hdrs = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    return {"type": "websocket", "server": list(server), "headers": hdrs}


def test_loopback_is_always_allowed():
    # Even a cross-origin-looking handshake over loopback is trusted local.
    allowed, _ = _ws_handshake_allowed(
        _scope(server=LOOPBACK, headers={"origin": "http://evil.example", "host": "anything"})
    )
    assert allowed is True


def test_remote_missing_origin_is_allowed_browser_scoped():
    # No Origin => non-browser client (no same-origin policy to bypass) => allowed.
    allowed, _ = _ws_handshake_allowed(_scope(headers={"host": "localhost"}))
    assert allowed is True


def test_remote_disallowed_origin_is_rejected():
    allowed, reason = _ws_handshake_allowed(
        _scope(headers={"origin": "http://evil.example", "host": "localhost"})
    )
    assert allowed is False
    assert reason.startswith("origin_not_allowed")


def test_remote_allowed_origin_passes(monkeypatch):
    monkeypatch.setenv("MCP_CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("MCP_TRUSTED_HOSTS", "app.example.com")
    allowed, _ = _ws_handshake_allowed(
        _scope(headers={"origin": "https://app.example.com", "host": "app.example.com"})
    )
    assert allowed is True


def test_remote_untrusted_host_is_rejected():
    # host not in the default trusted-hosts allowlist.
    allowed, reason = _ws_handshake_allowed(_scope(headers={"host": "attacker.example"}))
    assert allowed is False
    assert reason.startswith("host_not_allowed")


def test_remote_configured_host_passes(monkeypatch):
    monkeypatch.setenv("MCP_TRUSTED_HOSTS", "hangar.example.com")
    allowed, _ = _ws_handshake_allowed(_scope(headers={"host": "hangar.example.com:8000"}))
    assert allowed is True


def test_wildcard_trusted_hosts_disables_host_check(monkeypatch):
    monkeypatch.setenv("MCP_TRUSTED_HOSTS", "*")
    allowed, _ = _ws_handshake_allowed(_scope(headers={"host": "whatever.example"}))
    assert allowed is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("localhost:8000", "localhost"),
        ("[::1]:8000", "::1"),
        ("example.com", "example.com"),
        ("127.0.0.1", "127.0.0.1"),
        ("host.example ", "host.example"),
    ],
)
def test_strip_host_port(raw, expected):
    assert _strip_host_port(raw) == expected
