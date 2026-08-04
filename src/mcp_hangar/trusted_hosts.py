"""The set of hosts this process answers to.

One allowlist, read by every layer that needs it:

* `server.api.router` configures Starlette's `TrustedHostMiddleware` from it,
* `fastmcp_server.asgi` checks the MCP endpoint's Host against it,
* `auth.prm` uses it to decide whether a Host may become this resource's
  advertised RFC 9728 identity.

It lives in the shared kernel because those are three different layers, and the
alternative -- the same `os.environ.get("MCP_TRUSTED_HOSTS", ...)` parsed
separately in each -- is how the three drift apart. A security allowlist that
three call sites disagree about is worse than either answer consistently
applied: the disagreement is what an attacker gets to stand in.
"""

from __future__ import annotations

import os

DEFAULT_TRUSTED_HOSTS = "localhost,127.0.0.1,::1,testserver"

WILDCARD = "*"
"""Opts out of host checking entirely. `TrustedHostMiddleware`'s own escape hatch."""


def trusted_hosts() -> list[str]:
    """The configured allowlist, in `MCP_TRUSTED_HOSTS` order.

    Read at call time rather than at import: tests and reloads change the
    environment after this module is first imported, and a value cached at
    import would silently ignore them.
    """
    raw = os.environ.get("MCP_TRUSTED_HOSTS", DEFAULT_TRUSTED_HOSTS)
    return [h.strip() for h in raw.split(",") if h.strip()]


def strip_port(host: str) -> str:
    """The host part of a Host header, without its port."""
    return host.split(":")[0]


def host_is_trusted(host: str) -> bool:
    """Whether `host` is one this process is configured to serve.

    Matches `TrustedHostMiddleware`, including its `*.example.com` wildcard
    form, so the two cannot disagree about what counts as legitimate.
    """
    candidate = strip_port(host).lower()
    for allowed in trusted_hosts():
        entry = allowed.lower()
        if entry in (WILDCARD, candidate):
            return True
        if entry.startswith("*.") and candidate.endswith(entry[1:]):
            return True
    return False


def fallback_host() -> str:
    """A host to advertise when the request's own Host cannot be trusted.

    The first configured entry, so it is a value the operator chose. Skips a
    bare `*`, which names no host and cannot be advertised.
    """
    for host in trusted_hosts():
        if host != WILDCARD:
            return host
    return "localhost"
