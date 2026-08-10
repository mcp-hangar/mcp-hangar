"""`MCP_TRUSTED_HOSTS` governs the MCP endpoint, not only the REST API.

The allowlist module says it is read by every layer that needs it, and names
three. It was read by two: `TrustedHostMiddleware` on the REST API, and the
WebSocket handshake guard. The HTTP MCP endpoint had the SDK's own
DNS-rebinding guard instead, built from the SDK's default bind host -- so a
gateway answered `421 Invalid Host header` to its own Service DNS name while
that name was listed explicitly.

Measured on a released 2.5.1 replica before the fix, same request, only the
Host header varying:

    Host: 127.0.0.1:8080                            /mcp 200   /api/system/ 200
    Host: mcp-hangar.hangar.svc.cluster.local:8080  /mcp 421   /api/system/ 200

These assert the settings the endpoint is built with, because that is where the
divergence lived: both guards were working correctly, off different lists.
"""

from __future__ import annotations

import pytest

from mcp_hangar.fastmcp_server.asgi import mcp_transport_security


@pytest.fixture(autouse=True)
def _quiet_cors(monkeypatch):
    monkeypatch.setenv("MCP_CORS_ORIGINS", "https://console.example.com")


class TestTheAllowlistReachesTheTransportGuard:
    def test_a_configured_host_is_allowed(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "localhost,mcp-hangar.hangar.svc.cluster.local")

        settings = mcp_transport_security()

        assert "mcp-hangar.hangar.svc.cluster.local" in settings.allowed_hosts

    def test_a_configured_host_is_allowed_on_any_port(self, monkeypatch) -> None:
        # The SDK matches the raw Host header, so `example.internal` and
        # `example.internal:8080` are different entries -- while every other
        # check in Hangar strips the port. An operator writes a hostname; they
        # are served on a port. Without the expansion the fix would look
        # applied and still 421.
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "example.internal")

        settings = mcp_transport_security()

        assert "example.internal:*" in settings.allowed_hosts

    def test_an_unlisted_host_is_not_allowed(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "example.internal")

        settings = mcp_transport_security()

        assert settings.enable_dns_rebinding_protection is True
        assert not [h for h in settings.allowed_hosts if h.startswith("evil.")]

    def test_the_wildcard_opts_out(self, monkeypatch) -> None:
        # Same escape hatch TrustedHostMiddleware and the WebSocket guard honour.
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "*")

        assert mcp_transport_security().enable_dns_rebinding_protection is False

    def test_origins_come_from_the_cors_allowlist(self, monkeypatch) -> None:
        # A missing Origin passes in the SDK, so non-browser clients are
        # unaffected either way; a present one is held to the list the REST API
        # and the WebSocket handshake already use, rather than to a third.
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "example.internal")

        assert "https://console.example.com" in mcp_transport_security().allowed_origins


class TestBothServingPathsUseIt:
    """`serve --http` and the factory build the app separately.

    This repo has shipped the same class of bug repeatedly -- a capability
    wired into one construction path and not the other. Whichever path a
    deployment takes, the endpoint has to honour the allowlist.
    """

    @pytest.mark.parametrize(
        ("module", "attribute"),
        [
            ("mcp_hangar.server.lifecycle", "ServerLifecycle"),
            ("mcp_hangar.fastmcp_server.factory", "MCPServerFactory"),
        ],
    )
    def test_the_app_is_built_with_explicit_transport_security(self, module, attribute) -> None:
        import importlib
        import inspect

        source = inspect.getsource(getattr(importlib.import_module(module), attribute))

        assert "streamable_http_app(" in source
        assert "transport_security=mcp_transport_security()" in source, (
            f"{attribute} builds the MCP app without passing the configured allowlist; "
            "the SDK then derives one from its default bind host"
        )
