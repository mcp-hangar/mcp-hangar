"""Calls from the CLI to a running gateway's REST API (#1659).

`status`, `add` and `remove` each carried their own URL, and none of them was a
route the gateway serves: `/health`, `/reload` and `/MCP servers/{name}/stop`.
The calls answered 404 and the commands went on as if no gateway ran. This is
the one place that knows where the gateway is, how to authenticate, and what a
refusal looks like, so a command can say why instead of guessing.

Where the gateway is: `MCP_HANGAR_URL` when set, else `MCP_HTTP_HOST` and
`MCP_HTTP_PORT` (what `serve` binds), else the two ports `serve` is run on by
default. Authentication: `MCP_HANGAR_API_KEY`, sent as `X-API-Key`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

URL_ENV = "MCP_HANGAR_URL"
API_KEY_ENV = "MCP_HANGAR_API_KEY"
API_KEY_HEADER = "X-API-Key"
_DEFAULT_PORTS = (8000, 8080)


@dataclass(frozen=True)
class GatewayReply:
    """What the gateway answered, or why nothing did."""

    reached: bool
    status_code: int | None = None
    body: Any = None
    base_url: str | None = None

    @property
    def ok(self) -> bool:
        return self.reached and self.status_code is not None and 200 <= self.status_code < 300

    @property
    def refused_for_auth(self) -> bool:
        return self.status_code in (401, 403)

    def problem(self) -> str | None:
        """One line saying why the call did not succeed, or None when it did or no gateway answered."""
        if not self.reached or self.ok:
            return None
        if self.refused_for_auth:
            return (
                f"the gateway at {self.base_url} refused the request (HTTP {self.status_code}); "
                f"set {API_KEY_ENV} to an API key that may do this"
            )
        return f"the gateway at {self.base_url} answered HTTP {self.status_code}"


def candidate_urls() -> list[str]:
    """Base URLs to try, in order."""
    explicit = os.environ.get(URL_ENV, "").strip()
    if explicit:
        return [explicit.rstrip("/")]
    host = os.environ.get("MCP_HTTP_HOST", "").strip() or "127.0.0.1"
    if host in ("0.0.0.0", "::"):  # a bind address, not one to connect to
        host = "127.0.0.1"
    port = os.environ.get("MCP_HTTP_PORT", "").strip()
    ports = (int(port),) if port.isdigit() else _DEFAULT_PORTS
    return [f"http://{host}:{p}" for p in ports]


def call(method: str, path: str, *, json: Any = None, timeout: float = 5.0) -> GatewayReply:
    """Call `path` (for example `/api/mcp_servers/`) on the first gateway that answers."""
    headers = {}
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        headers[API_KEY_HEADER] = key
    for base in candidate_urls():
        try:
            response = httpx.request(method, f"{base}{path}", json=json, headers=headers, timeout=timeout)
        except httpx.TransportError:
            continue
        try:
            body = response.json()
        except ValueError:
            body = None
        return GatewayReply(reached=True, status_code=response.status_code, body=body, base_url=base)
    return GatewayReply(reached=False)
