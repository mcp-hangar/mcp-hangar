"""A front_door gateway for the server conformance suite, with one caller's key added (#2307).

    python tests/conformance/front_door_gateway.py --upstream URL --protocol auto|legacy --port PORT

The server legs in conformance.yml judge the default egress topology, which
exposes no upstream tool under its own name. This one judges the topology
that does: ``serve --http`` with ``tool_access.mode: front_door``, auth on,
and the suite's reference everything-server as its only upstream, every tool
of it projected flat.

A front door serves a tenant's projection, so an anonymous caller is offered
no tool at all, by design. ``conformance server`` takes only ``--url`` and
cannot present a credential. So this seeds one API key for one principal and
puts a loopback proxy in front of the gateway that adds that key as
``X-API-Key`` to every request and changes nothing else: method, path, query,
the other headers (``Host`` included) and the body go through as sent, and the
response comes back as the gateway wrote it, streamed. The proxy is test
plumbing, not part of what is judged; what is judged is the gateway answering
an authenticated caller.

The gateway listens on a free loopback port; the proxy on ``--port``, which is
the URL the suite gets. Both stop when this process is terminated.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Every tool the reference server lists on either revision. A name the upstream
#: does not serve on the revision in use projects nothing.
FLAT = [
    "test_simple_text",
    "test_image_content",
    "test_audio_content",
    "test_embedded_resource",
    "test_multiple_content_types",
    "test_tool_with_logging",
    "test_tool_with_progress",
    "test_error_handling",
    "test_reconnection",
    "test_sampling",
    "test_elicitation",
    "test_elicitation_sep1034_defaults",
    "test_elicitation_sep1330_enums",
    "json_schema_2020_12_tool",
    "test_missing_capability",
    "test_input_required_result_elicitation",
    "test_input_required_result_sampling",
    "test_input_required_result_list_roots",
    "test_input_required_result_request_state",
    "test_input_required_result_multiple_inputs",
    "test_input_required_result_multi_round",
    "test_input_required_result_tampered_state",
    "test_input_required_result_capabilities",
    "test_streaming_elicitation",
    "test_logging_tool",
]
TENANT = "conformance"
PRINCIPAL = f"svc:{TENANT}"
SERVER = "everything"
# Hop-by-hop headers (RFC 9110, 7.6.1) and the ones the HTTP client recomputes.
_HOP = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _config(upstream: str, protocol: str, auth_db: Path) -> str:
    flat = ", ".join(FLAT)
    return f"""\
logging:
  level: WARNING
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {json.dumps(str(auth_db))}
  role_assignments:
    - principal: "{PRINCIPAL}"
      role: developer
      scope: global
mcp_servers:
  {SERVER}:
    mode: remote
    endpoint: {json.dumps(upstream)}
    upstream_protocol: {protocol}
    idle_ttl_s: 600
    tool_projection:
      flat: [{flat}]
"""


def _seed_key(auth_db: Path) -> str:
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        return store.create_key(principal_id=PRINCIPAL, name="conformance", tenant_id=TENANT)
    finally:
        store.close()


def _proxy(gateway: str, api_key: str):  # type: ignore[no-untyped-def]
    """An ASGI app that forwards every request to *gateway* with the key added."""
    import httpx
    from starlette.background import BackgroundTask
    from starlette.requests import Request
    from starlette.responses import StreamingResponse

    client = httpx.AsyncClient(base_url=gateway, timeout=httpx.Timeout(None))

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _HOP | {"x-api-key"}]
        headers.append(("x-api-key", api_key))
        upstream = client.build_request(
            request.method,
            request.url.path,
            params=request.url.query,
            headers=headers,
            content=request.stream(),
        )
        response = await client.send(upstream, stream=True)
        await StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() not in _HOP},
            background=BackgroundTask(response.aclose),
        )(scope, receive, send)

    return app


def _stateless(url: str, method: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """One 2026-07-28 request, answered as JSON; raises OSError on anything but a 200."""
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "front-door-harness", "version": "1"},
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"_meta": meta}}).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    for name, value in {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        **(headers or {}),
    }.items():
        request.add_header(name, value)
    with urllib.request.urlopen(request, timeout=10) as response:
        return dict(json.loads(response.read()))


def _wait(what: str, ready: Callable[[], bool], seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if ready():
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise SystemExit(f"{what} never became ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream", required=True, help="the reference server's MCP endpoint")
    parser.add_argument("--protocol", choices=("auto", "legacy"), default="auto")
    parser.add_argument("--port", type=int, required=True, help="where the suite connects")
    parser.add_argument("--log", type=Path, default=Path("gateway.log"))
    args = parser.parse_args()

    import uvicorn

    # The front door warms its upstreams once, at boot: an upstream that is not
    # answering yet leaves the projection empty for the whole run.
    _wait("the reference server", lambda: "result" in _stateless(args.upstream, "server/discover"))
    with tempfile.TemporaryDirectory() as tmp:
        auth_db = Path(tmp) / "auth.db"
        api_key = _seed_key(auth_db)
        config = Path(tmp) / "config.yaml"
        config.write_text(_config(args.upstream, args.protocol, auth_db), encoding="utf-8")
        port = _free_port()
        gateway = f"http://127.0.0.1:{port}"
        cli = str(Path(sys.executable).with_name("mcp-hangar"))
        with open(args.log, "w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [cli, "--config", str(config), "serve", "--http", "--host", "127.0.0.1", "--port", str(port)],
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                # Healthy is not enough: the suite lists tools at once, so the
                # proxy opens only when the caller's projection holds the
                # reference server's tools.
                def projected() -> bool:
                    listed = _stateless(f"{gateway}/mcp", "tools/list", {"X-API-Key": api_key})
                    return FLAT[0] in {t.get("name") for t in listed.get("result", {}).get("tools", [])}

                _wait("the front door's projection", projected)
                uvicorn.run(_proxy(gateway, api_key), host="127.0.0.1", port=args.port, log_level="warning")
            finally:
                process.terminate()
                process.wait(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
