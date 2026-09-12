"""Which client address Hangar acts on, now that uvicorn does not rewrite it.

Hangar used to let uvicorn replace a loopback peer (or any in
``FORWARDED_ALLOW_IPS``) with its ``X-Forwarded-For`` address before any Hangar
code ran, and then applied ``MCP_TRUSTED_PROXIES`` to what was left. A loopback
proxy therefore never looked like a proxy: its ``x-session-id`` was ignored and
a suspension could not match (GHSA-fhwh-fmq2-7m5c).

With uvicorn's handling off, ``TrustedProxyResolver`` is the one decision, and
it has to get the address right on its own -- including for a proxy that
appends to an ``X-Forwarded-For`` the client already wrote, which is where
taking the first entry would let a client choose the address the auth rate
limiter and lockout count against. These tests pin that for the resolver and
for the auth middleware that feeds rate limiting and security events.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware
from mcp_hangar.domain.events.auth import AuthenticationFailed
from mcp_hangar.domain.exceptions import InvalidCredentialsError
from mcp_hangar.infrastructure.identity.trusted_proxy import TrustedProxyResolver, resolve_source_ip
from mcp_hangar.server.api.middleware import AuthEnforcementMiddleware

_LOOPBACK = frozenset({"127.0.0.1", "::1"})


@pytest.fixture(autouse=True)
def default_trusted_proxies(monkeypatch):
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)


_CASES = [
    # (why, trusted proxies, peer, X-Forwarded-For, expected)
    ("a direct client is itself", _LOOPBACK, "198.51.100.10", None, "198.51.100.10"),
    ("an untrusted peer's header is ignored", _LOOPBACK, "203.0.113.9", "198.51.100.7", "203.0.113.9"),
    ("a trusted loopback proxy forwards the client", _LOOPBACK, "127.0.0.1", "198.51.100.7", "198.51.100.7"),
    (
        "a proxy appending to a client-written header forwards the address it saw",
        _LOOPBACK,
        "127.0.0.1",
        "10.9.9.9, 198.51.100.7",
        "198.51.100.7",
    ),
    (
        "trusted hops are skipped from the right",
        _LOOPBACK | {"10.0.0.0/8"},
        "127.0.0.1",
        "198.51.100.7, 10.0.0.5",
        "198.51.100.7",
    ),
    ("every hop trusted: the leftmost", _LOOPBACK | {"10.0.0.0/8"}, "127.0.0.1", "10.0.0.6, 10.0.0.5", "10.0.0.6"),
    ("a blank header is no header", _LOOPBACK, "127.0.0.1", " , ", "127.0.0.1"),
    ("an IPv6 loopback proxy is trusted too", _LOOPBACK, "::1", "2001:db8::7", "2001:db8::7"),
]


class TestResolveSourceIp:
    @pytest.mark.parametrize(("why", "proxies", "peer", "forwarded", "expected"), _CASES, ids=[c[0] for c in _CASES])
    def test_the_address(self, why, proxies, peer, forwarded, expected) -> None:
        headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}

        got = resolve_source_ip(headers=headers, client_host=peer, trusted_proxies=TrustedProxyResolver(proxies))

        assert got == expected, why

    def test_no_peer_is_the_default(self) -> None:
        assert resolve_source_ip(headers={}, client_host=None, trusted_proxies=TrustedProxyResolver(_LOOPBACK)) == (
            "unknown"
        )


class _Refusing:
    """An authenticator that takes every request and refuses it."""

    def supports(self, _request: Any) -> bool:
        return True

    def authenticate(self, _request: Any) -> Any:
        raise InvalidCredentialsError(message="bad credential", auth_method="test")


class _Limiter:
    """The rate limiter's surface, recording the key each call is made with."""

    def __init__(self) -> None:
        self.checked: list[str] = []
        self.failed: list[str] = []

    def check_rate_limit(self, ip: str) -> Any:
        self.checked.append(ip)
        return SimpleNamespace(allowed=True, reason=None, retry_after=None)

    def record_failure(self, ip: str) -> None:
        self.failed.append(ip)

    def record_success(self, ip: str) -> None:  # pragma: no cover -- every request here fails
        raise AssertionError(ip)


async def _drive(peer: str, forwarded: str | None) -> tuple[_Limiter, list[Any], list[dict[str, Any]]]:
    """One request through the served auth wrapper, as uvicorn now hands it over."""
    limiter = _Limiter()
    events: list[Any] = []
    authn = AuthenticationMiddleware([_Refusing()], rate_limiter=limiter, event_publisher=events.append)

    async def app(_scope, _receive, _send):  # pragma: no cover -- auth refuses first
        raise AssertionError("reached the app")

    middleware = AuthEnforcementMiddleware(app, authn=authn)
    headers = [(b"host", b"gateway.internal")]
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/system/me",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 51000),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return limiter, events, sent


_SERVED = [
    ("a direct client", "198.51.100.10", None, "198.51.100.10"),
    ("a trusted loopback proxy", "127.0.0.1", "198.51.100.7", "198.51.100.7"),
    ("a loopback proxy appending to a client-written header", "127.0.0.1", "10.9.9.9, 198.51.100.7", "198.51.100.7"),
    ("an untrusted peer sending the header", "203.0.113.9", "198.51.100.7", "203.0.113.9"),
]


class TestTheAuthMiddlewareActsOnTheResolvedAddress:
    @pytest.mark.parametrize(("why", "peer", "forwarded", "expected"), _SERVED, ids=[c[0] for c in _SERVED])
    async def test_rate_limiting_and_the_security_event_see_it(self, why, peer, forwarded, expected) -> None:
        limiter, events, sent = await _drive(peer, forwarded)

        assert sent[0]["status"] == 401
        assert limiter.checked == [expected], why
        assert limiter.failed == [expected], why
        failures = [event for event in events if isinstance(event, AuthenticationFailed)]
        assert [event.source_ip for event in failures] == [expected], why

    async def test_a_rotating_client_written_prefix_does_not_rotate_the_key(self) -> None:
        # The lockout evasion taking the leftmost entry allowed: a client that
        # changes what it writes in front of the proxy's entry every attempt.
        keys = set()
        for spoof in ("10.1.1.1", "10.2.2.2", "10.3.3.3"):
            limiter, _events, _sent = await _drive("127.0.0.1", f"{spoof}, 198.51.100.7")
            keys.update(limiter.failed)

        assert keys == {"198.51.100.7"}
