"""A stale name is told to list again, over the app ``serve --http`` serves (#1368).

The unit tests drive the two handlers with a stand-in request context. On this
front door that shape has hidden fail-opens before: the identity is re-bound per
request, in a task the ASGI wrapper does not own, and the SDK lists tools by
itself before it dispatches a call (#1049). So everything here goes over the real
streamable-HTTP transport:

* The composition ``serve --http`` serves,
  ``mcp_app_for_serving(build_serving_mcp_server())``, behind the authentication
  layer it mounts (``create_auth_enforced_app``). Each tenant authenticates with
  its own API key.
* One real upstream: an in-process HTTP MCP server behind a real ``McpServer``.
  It is started through the command bus, and its catalogue lands in the
  projection registry through the ``McpServerStarted`` handler bootstrap
  subscribes.
* The projection changes for real: a withdrawal through the registry, a policy
  edit through the resolver, and a fleet in which the tool does not exist at all.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import catalogue_warmup, flat_tool_projection, served_tool_names
from mcp_hangar.fastmcp_server.served_tool_names import ServedNames

# The SDK's DNS-rebinding protection wants a loopback Host with a port.
_BASE_URL = "http://127.0.0.1:8000"
_MODERN = "2026-07-28"
_LEGACY = "2025-06-18"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "stale-name-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

SERVER = "store"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"
METHOD_NOT_FOUND = -32601
REASON = {"reason": "projection_changed"}


def _ordinary(name: str) -> dict[str, Any]:
    """The ``-32601`` every call to a name the caller cannot call has always had."""
    return {"code": METHOD_NOT_FOUND, "message": f"Tool '{name}' not found"}


class _Upstream(BaseHTTPRequestHandler):
    """A minimal JSON-answering MCP upstream that records the tools it is asked to call."""

    tools: ClassVar[tuple[str, ...]] = ()
    called: ClassVar[list[str]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
        if method == "tools/call":
            self.called.append(params.get("name"))
        definitions = [
            {"name": name, "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}}
            for name in self.tools
        ]
        answer = {
            "initialize": {
                "result": {
                    "protocolVersion": _LEGACY,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "upstream", "version": "0"},
                }
            },
            "tools/list": {"result": {"tools": definitions}},
            "tools/call": {"result": {"content": [{"type": "text", "text": f"did {params.get('name')}"}]}},
        }.get(method, {"error": {"code": METHOD_NOT_FOUND, "message": f"Unknown method: {method}"}})
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _jsonrpc(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC payload, from either framing (plain JSON or an SSE frame).

    The status is not asserted here. The modern entry answers a JSON-RPC error
    with the HTTP status the SDK maps its code to (404 for ``-32601``), and the
    handshake era answers 200 on the SSE framing. Where the status is part of
    what a caller could tell apart, the test compares it.
    """
    text = response.text.lstrip()
    if text.startswith("{"):
        return dict(json.loads(text))
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[len("data: ") :]))
    raise AssertionError(f"neither JSON nor an SSE data frame: {text[:200]!r}")


@dataclass
class _FrontDoor:
    client: TestClient
    keys: dict[str, str]
    upstream: type[_Upstream]

    def post(
        self, tenant: str, method: str, params: dict[str, Any], *, era: str = _MODERN, request_id: int = 1
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": era,
            "X-API-Key": self.keys[tenant],
        }
        body_params = dict(params)
        if era == _MODERN:
            # Self-describing and SEP-2243 routed: the modern era has no handshake.
            headers["Mcp-Method"] = method
            if method == "tools/call":
                headers["Mcp-Name"] = params["name"]
            body_params["_meta"] = _ENVELOPE
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
        return self.client.post("/mcp", headers=headers, content=json.dumps(body))

    def names(self, tenant: str, *, era: str = _MODERN) -> list[str]:
        """``tools/list`` as the client receives it."""
        return sorted(
            tool["name"] for tool in _jsonrpc(self.post(tenant, "tools/list", {}, era=era))["result"]["tools"]
        )

    def call(
        self,
        tenant: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        era: str = _MODERN,
        request_id: int = 1,
    ) -> httpx.Response:
        params = {"name": name, "arguments": arguments or {}}
        return self.post(tenant, "tools/call", params, era=era, request_id=request_id)

    def error(self, tenant: str, name: str, *, era: str = _MODERN) -> dict[str, Any]:
        payload = _jsonrpc(self.call(tenant, name, era=era))
        assert "error" in payload, payload
        return dict(payload["error"])

    def result(self, tenant: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = _jsonrpc(self.call(tenant, name, arguments))
        assert "result" in payload, payload
        assert not payload["result"].get("isError"), payload
        return dict(payload["result"])


def _runtime(endpoint: str) -> Any:
    """The fleet: one remote upstream, the two commands the invoke path sends, the projection handler."""
    from mcp_hangar.application.commands import InvokeToolCommand, StartMcpServerCommand
    from mcp_hangar.application.commands.handlers import InvokeToolHandler, StartMcpServerHandler
    from mcp_hangar.application.event_handlers.tool_projection_handler import ToolProjectionPopulationHandler
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStarted
    from mcp_hangar.domain.model import McpServer
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.server.context import init_context

    bus = CommandBus()
    runtime = create_runtime(command_bus=bus)
    bus.register(StartMcpServerCommand, StartMcpServerHandler(runtime.repository, runtime.event_bus))
    bus.register(InvokeToolCommand, InvokeToolHandler(runtime.repository, runtime.event_bus))
    # Subscribed the way bootstrap subscribes it (`server/bootstrap/event_handlers.py`).
    projection = ToolProjectionPopulationHandler(repository=runtime.repository)
    runtime.event_bus.subscribe(McpServerStarted, projection.handle, kind=HandlerKind.LOCAL_VIEW)
    runtime.repository.add(SERVER, McpServer(mcp_server_id=SERVER, mode="remote", endpoint=endpoint))
    init_context(runtime)
    bus.send(StartMcpServerCommand(mcp_server_id=SERVER))
    return runtime


@contextmanager
def _front_door(tools: tuple[str, ...], policies: dict[str, tuple[str, ...]] | None = None) -> Iterator[_FrontDoor]:
    """A served front door over one upstream exposing *tools*, each tenant with an API key.

    *policies* maps a tenant to its allow-list on the upstream; a tenant without
    one is allowed everything.
    """
    from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
    from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import build_serving_mcp_server
    from mcp_hangar.server.context import reset_context
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    reset_tool_projection_registry()
    reset_tool_access_resolver()
    catalogue_warmup.reset()
    resolver = get_tool_access_resolver()
    resolver.set_topology_mode("front_door")
    for tenant, allowed in (policies or {}).items():
        resolver.set_standalone_member_policy(SERVER, tenant, ToolAccessPolicy(allow_list=allowed))

    handler: type[_Upstream] = type("_ThisUpstream", (_Upstream,), {"tools": tools, "called": []})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    runtime = None
    try:
        runtime = _runtime(f"http://127.0.0.1:{upstream.server_address[1]}/mcp")
        discovered = sorted(projection.tool for projection in get_tool_projection_registry().all())
        assert discovered == sorted(tools), f"the upstream's catalogue did not reach the registry: {discovered}"

        store = InMemoryApiKeyStore()
        keys = {
            tenant: store.create_key(principal_id=f"agent-{label}", name=f"key-{label}", tenant_id=tenant)
            for label, tenant in (("a", TENANT_A), ("b", TENANT_B))
        }
        auth = SimpleNamespace(authn_middleware=AuthenticationMiddleware([ApiKeyAuthenticator(store)]))
        app = create_auth_enforced_app(mcp_app_for_serving(build_serving_mcp_server()), auth)
        with TestClient(app, base_url=_BASE_URL) as client:
            yield _FrontDoor(client, keys, handler)
    finally:
        if runtime is not None:
            for server in runtime.repository.get_all().values():
                server.shutdown()
        upstream.shutdown()
        upstream.server_close()
        reset_context()
        reset_tool_projection_registry()
        reset_tool_access_resolver()
        catalogue_warmup.reset()


@pytest.fixture(autouse=True)
def _fresh_memory(monkeypatch) -> None:
    monkeypatch.setattr(served_tool_names, "SERVED", ServedNames())


class TestTheFullSequence:
    def test_list_change_stale_call_relist_and_the_fresh_list_is_callable(self) -> None:
        """List, the projection changes, call the stale name, get the reason, list again, call."""
        with _front_door(("read_item", "write_item"), {TENANT_A: ("write_item",)}) as front_door:
            assert front_door.names(TENANT_A) == ["write_item"]

            # The projection changes under the connected client: an operator edits its policy.
            get_tool_access_resolver().set_standalone_member_policy(
                SERVER, TENANT_A, ToolAccessPolicy(allow_list=("read_item",))
            )

            assert front_door.error(TENANT_A, "write_item") == {**_ordinary("write_item"), "data": REASON}

            assert front_door.names(TENANT_A) == ["read_item"]
            result = front_door.result(TENANT_A, "read_item")
            assert result["content"] == [{"type": "text", "text": "did read_item"}]
            assert front_door.upstream.called == ["read_item"]

    def test_a_name_that_really_is_gone_is_the_ordinary_error_after_listing_again(self) -> None:
        with _front_door(("read_item", "write_item")) as front_door:
            assert front_door.names(TENANT_A) == ["read_item", "write_item"]

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            assert front_door.error(TENANT_A, "write_item") == {**_ordinary("write_item"), "data": REASON}
            assert front_door.names(TENANT_A) == ["read_item"]
            assert front_door.error(TENANT_A, "write_item") == _ordinary("write_item")
            assert front_door.upstream.called == []

    def test_a_handshake_era_client_gets_the_reason_too(self) -> None:
        """The legacy era answers on the SSE framing, through the front-door wrap rather than the modern entry."""
        with _front_door(("read_item", "write_item")) as front_door:
            assert front_door.names(TENANT_A, era=_LEGACY) == ["read_item", "write_item"]

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            assert front_door.error(TENANT_A, "write_item", era=_LEGACY) == {**_ordinary("write_item"), "data": REASON}
            assert front_door.names(TENANT_A, era=_LEGACY) == ["read_item"]
            assert front_door.error(TENANT_A, "write_item", era=_LEGACY) == _ordinary("write_item")


class TestDeniedAndAbsentStayIndistinguishable:
    """#905: a caller must not learn that a name exists for someone else."""

    def test_tenant_b_cannot_tell_a_tool_it_is_denied_from_one_that_exists_nowhere(self) -> None:
        # Tenant A holds write_item and has it withdrawn. Tenant B is denied it by
        # policy and so was never served it.
        with _front_door(("read_item", "write_item"), {TENANT_B: ("read_item",)}) as front_door:
            assert front_door.names(TENANT_A) == ["read_item", "write_item"]
            assert front_door.names(TENANT_B) == ["read_item"]
            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            withdrawn_from_a = front_door.call(TENANT_A, "write_item", request_id=7)
            denied_to_b = front_door.call(TENANT_B, "write_item", request_id=7)
            never_existed_for_b = front_door.call(TENANT_B, "no_such_item", request_id=7)

        # The same caller, the same configuration and the same history, in a fleet
        # where write_item exists for nobody.
        with _front_door(("read_item",), {TENANT_B: ("read_item",)}) as front_door:
            assert front_door.names(TENANT_B) == ["read_item"]
            absent_for_b = front_door.call(TENANT_B, "write_item", request_id=7)

        # The full wire answer, byte for byte: status, framing and body.
        assert denied_to_b.status_code == absent_for_b.status_code
        assert denied_to_b.headers["content-type"] == absent_for_b.headers["content-type"]
        assert denied_to_b.content == absent_for_b.content
        assert _jsonrpc(denied_to_b) == _jsonrpc(absent_for_b)

        # Denied is the ordinary error, shaped exactly like one for a name nobody has.
        assert _jsonrpc(denied_to_b)["error"] == _ordinary("write_item")
        assert _jsonrpc(never_existed_for_b)["error"] == _ordinary("no_such_item")

        # Only the caller that was served the name hears that its list is stale.
        assert _jsonrpc(withdrawn_from_a)["error"] == {**_ordinary("write_item"), "data": REASON}

    def test_the_reason_names_no_upstream_no_tool_and_no_count(self) -> None:
        with _front_door(("read_item", "write_item"), {TENANT_B: ("read_item",)}) as front_door:
            front_door.names(TENANT_A)
            front_door.names(TENANT_B)
            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            data = front_door.error(TENANT_A, "write_item")["data"]

        assert data == REASON
        carried = json.dumps(data)
        for named in (SERVER, "write_item", "read_item", TENANT_A, TENANT_B, "upstream"):
            assert named not in carried, f"the reason carries {named!r}"
        assert not any(character.isdigit() for character in carried), f"the reason carries a number: {carried}"


class TestOnlyAListingTheClientReceivedCounts:
    def test_the_sdks_own_listing_before_a_call_is_not_remembered(self, monkeypatch) -> None:
        """A 2026-07-28 call with arguments makes the SDK list first (#1049). The client received nothing."""
        listings: list[str | None] = []
        generate = flat_tool_projection.generate_projection

        def _counting(tenant_id: str | None) -> Any:
            listings.append(tenant_id)
            return generate(tenant_id)

        monkeypatch.setattr(flat_tool_projection, "generate_projection", _counting)

        with _front_door(("read_item", "write_item")) as front_door:
            assert front_door.result(TENANT_A, "read_item", {"x": "1"})["content"] == [
                {"type": "text", "text": "did read_item"}
            ]
            assert listings == [TENANT_A], "the SDK did not list before the call, so this test would prove nothing"

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

            assert front_door.error(TENANT_A, "write_item") == _ordinary("write_item")
            assert len(served_tool_names.SERVED) == 0
