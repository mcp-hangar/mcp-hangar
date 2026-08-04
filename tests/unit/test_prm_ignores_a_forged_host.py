"""The advertised resource identity must not come from an attacker's Host header.

RFC 9728 metadata tells a client which resource it is authenticating to. Hangar
derives that from the request's Host when `auth.oidc.resource_uri` is not
configured -- and the Host header is set by the caller.

Both call sites are reached *before* any host check:

* `AuthMiddlewareHTTP` builds the `WWW-Authenticate` challenge, and it is added
  to the app AFTER `TrustedHostMiddleware`, which in Starlette means it wraps
  it -- so it runs first, on an unvalidated Host.
* the `.well-known` PRM endpoint on the serving app has no `TrustedHostMiddleware`
  at all.

So a forged Host was echoed back as this resource's identity, in the document
clients use to decide where to send tokens.

Found by an independent model review during a security audit (LLM-03), and
confirmed by reading the middleware order rather than taking the finding at face
value -- `TrustedHostMiddleware` is present, it simply does not cover this path.

The fix ignores an untrusted Host instead of reflecting it, falling back to a
host the operator configured. That degrades to a value someone chose: a client
that cannot reach it fails to authenticate, rather than authenticating somewhere
an attacker named.
"""

from __future__ import annotations

import pytest

from mcp_hangar.auth.prm import build_resource_base_url


def _scope(host: str, forwarded_proto: str | None = None, scheme: str = "http") -> dict:
    headers = [(b"host", host.encode())]
    if forwarded_proto is not None:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return {"headers": headers, "scheme": scheme}


class TestATrustedHostIsHonoured:
    """The fix must not break the legitimate derivation it is guarding."""

    @pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8124"])
    def test_a_configured_host_passes_through_with_its_port(self, host):
        assert build_resource_base_url(_scope(host)) == f"http://{host}"

    def test_a_proxy_can_still_declare_https(self):
        assert build_resource_base_url(_scope("localhost", "https")) == "https://localhost"


class TestAForgedHostIsIgnored:
    @pytest.mark.parametrize(
        "host",
        ["evil.example.com", "attacker.test", "localhost.evil.com", "127.0.0.1.evil.com"],
    )
    def test_it_does_not_reach_the_advertised_identity(self, host):
        result = build_resource_base_url(_scope(host))
        assert host not in result, (
            f"a forged Host {host!r} is advertised as this resource's identity; a client "
            "reading the PRM document would send its token there"
        )

    def test_the_fallback_is_a_configured_host(self, monkeypatch):
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "gateway.internal,localhost")
        assert build_resource_base_url(_scope("evil.example.com")) == "http://gateway.internal"

    def test_a_wildcard_entry_still_matches_its_domain(self, monkeypatch):
        """Mirrors TrustedHostMiddleware, so the two cannot disagree on what is legitimate."""
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "*.example.com")
        assert build_resource_base_url(_scope("api.example.com")) == "http://api.example.com"
        assert "evil.test" not in build_resource_base_url(_scope("evil.test"))

    def test_an_operator_can_opt_out_entirely(self, monkeypatch):
        """`*` is TrustedHostMiddleware's own escape hatch; honour it rather than diverge."""
        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "*")
        assert build_resource_base_url(_scope("anything.test")) == "http://anything.test"


class TestTheSchemeIsAlsoCallerControlled:
    """`x-forwarded-proto` is a header too, and it lands in the same URL."""

    @pytest.mark.parametrize("proto", ["javascript", "file", "gopher", "", "HTTPS evil"])
    def test_a_scheme_that_is_not_http_or_https_is_refused(self, proto):
        result = build_resource_base_url(_scope("localhost", proto))
        assert result.startswith(("http://", "https://")), result
        assert proto not in result or proto in ("", "http", "https")


class TestTheAllowlistHasOneSource:
    """Three layers decide "is this host legitimate". They must decide it identically.

    Before this fix the same `os.environ.get("MCP_TRUSTED_HOSTS", <default>)`
    was parsed in three places, and this fix would have made it four. A security
    allowlist that its call sites disagree about is worse than either answer
    applied consistently -- the disagreement is the gap an attacker stands in.
    """

    CALL_SITES = [
        "src/mcp_hangar/server/api/router.py",
        "src/mcp_hangar/fastmcp_server/asgi.py",
        "src/mcp_hangar/auth/prm.py",
    ]

    @pytest.mark.parametrize("path", CALL_SITES)
    def test_no_call_site_parses_the_variable_itself(self, path):
        """Naming the variable in a docstring is fine; reading it is not."""
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
        reads = [
            node for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == "MCP_TRUSTED_HOSTS"
        ]
        assert reads == [], (
            f"{path} reads MCP_TRUSTED_HOSTS directly instead of going through "
            "mcp_hangar.trusted_hosts; a second parse is a second answer"
        )

    @pytest.mark.parametrize("path", CALL_SITES)
    def test_every_call_site_imports_the_shared_module(self, path):
        import pathlib

        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert "trusted_hosts import" in source, f"{path} no longer uses the shared allowlist"

    def test_the_default_still_covers_local_development(self):
        """The default is what runs with nothing configured; it must not lock the operator out."""
        from mcp_hangar.trusted_hosts import host_is_trusted

        assert host_is_trusted("localhost") and host_is_trusted("127.0.0.1")

    def test_the_fallback_never_advertises_a_wildcard(self, monkeypatch):
        """`*` names no host, so it cannot become an advertised identity."""
        from mcp_hangar.trusted_hosts import fallback_host

        monkeypatch.setenv("MCP_TRUSTED_HOSTS", "*,gateway.internal")
        assert fallback_host() == "gateway.internal"
