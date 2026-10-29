"""Boot Hangar once, then check that what each caller is shown is what it may call (#1785).

Run as a script, in its own interpreter, by ``test_what_a_caller_is_shown_is_what_it_may_call.py``:
``python _listing_call_parity_harness.py <topology> <out.json>``. Not collected by pytest.

A separate process for the reason ``_route_decisions_harness.py`` gives:
``bootstrap()`` fills process-global state, and the topology mode cannot change
after it. One process per topology, so the egress and front-door surfaces are
each the app ``serve --http`` serves for that mode.

What runs is production, and nothing is patched:

- ``bootstrap()``, reading a config file. API-key auth is on; three tenants
  hold the ``developer`` role;
- the app ``serve --http`` serves, wrapped in the auth enforcement ``run_http``
  applies, under starlette's ``TestClient``;
- the Python facade's ``invoke``, over the same context (built over it as
  ``test_facade.py`` builds one over a pre-initialized context);
- three in-process HTTP MCP upstreams. ``alpha`` and ``beta`` serve the same
  catalogue and are the members of the group ``pool`` (priority strategy, so a
  load-balanced call is routed to ``alpha`` while it is selectable); ``gamma``
  stands alone with a catalogue of its own.

Hypothesis generates the governance of that topology: tool policies on each
server, on the group and on each member, tenant-scoped policies, withdrawals of
every kind for all tenants or one, prompt and resource ``access:`` policies,
canary pins, and block-mode digest pins. Each example is applied by the
production reload (``ReloadConfigurationCommand``), which swaps governance
without restarting a server; the harness checks that it restarted none.

For every surface, one Hypothesis run, and per surface a report: passed, or the
shrunk falsifying example and the assertion it broke. The examples are
derandomized and shared across surfaces, and each example's observations are
computed once, so a surface run after the first costs only its comparison.

The invariant, as the tracker review on #1785 narrowed it, per caller, target
and kind, on the governance generation the example applied. Names are compared,
not arguments: an argument-dependent L7 refusal cannot be predicted from a name.

- *listed is callable*: no call path refuses a name a surface lists;
- *callable is listed*: a tool with one route (a server named directly, a
  pinned tenant) is listed exactly when the facade's call of it is served. Where
  a group may route the caller to several members, the listing is exactly what
  ``decide()`` allows on every one of them (``route_candidates``, decision 1 on
  #1763), and no call is served a name no route allows. Prompts and resources
  are held to ``decide()`` on every route, since their fetch shares the
  listing's predicate and is no independent witness; the fetch is then held to
  refuse nothing ``decide()`` allows on its one route;
- the call paths agree: ``hangar_call`` and the facade's ``invoke`` reach the
  same outcome, and the front door's flat call is served exactly when the
  facade's call of the same name on the same target is, except where decision 1
  lets a load-balanced call be served a name the front door does not list;
- on the front door, a name a caller may not call is refused as an unknown
  name is (#905). ``hangar_call`` on egress names its refusal by design, and is
  not held to this;
- ``hangar_details`` names a withheld tool (#1776: an operator diagnostic, not
  an invocable entry, so only ``tools`` is compared) only when the caller may
  list it;
- every refusal is reported with its class: ``authorization`` (access policy,
  withdrawal, unknown name), ``digest`` (pin drift) or ``availability``.
  Approval is left out of the generator: a held call waits for an approver.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
EGRESS, FRONT_DOOR = "egress", "front_door"

GROUP, ALPHA, BETA, GAMMA = "pool", "alpha", "beta", "gamma"
MEMBERS = (ALPHA, BETA)
SERVERS = (ALPHA, BETA, GAMMA)
TARGETS = (ALPHA, BETA, GAMMA, GROUP)
TENANTS = ("tenant-lb", "tenant-one", "tenant-two")

#: What each upstream serves. `alpha` and `beta` are interchangeable members of
#: `pool`, so they serve one catalogue; `gamma`'s names collide with nothing.
POOL_TOOLS = ("add", "divide", "power")
GAMMA_TOOLS = ("ping", "purge")
POOL_PROMPTS, GAMMA_PROMPTS = ("greet", "audit"), ("brief",)
POOL_RESOURCES, GAMMA_RESOURCES = ("doc://pool/one", "doc://pool/two"), ("doc://gamma/one",)
CATALOGUES: dict[str, dict[str, tuple[str, ...]]] = {
    ALPHA: {"tool": POOL_TOOLS, "prompt": POOL_PROMPTS, "resource": POOL_RESOURCES},
    BETA: {"tool": POOL_TOOLS, "prompt": POOL_PROMPTS, "resource": POOL_RESOURCES},
    GAMMA: {"tool": GAMMA_TOOLS, "prompt": GAMMA_PROMPTS, "resource": GAMMA_RESOURCES},
}
CATALOGUES[GROUP] = CATALOGUES[ALPHA]
#: Names no upstream serves, for the non-disclosure check (#905).
#: The unknown resource is under an id no upstream has: a URI under a visible
#: upstream is relayed, and the upstream's own not-found answer is its own text.
UNKNOWN_TOOL, UNKNOWN_PROMPT, UNKNOWN_RESOURCE = "no_such_tool", "no_such_prompt", "doc://nowhere/none"

#: The surfaces each topology serves. The parent test parametrizes over these.
SURFACES = {
    EGRESS: ("hangar_tools", "hangar_details", "hangar_details_group", "hangar_call", "facade_invoke"),
    FRONT_DOOR: (
        "tools_list",
        "server_discover",
        "prompts_list",
        "resources_list",
        "hangar_tools",
        "hangar_details",
        "hangar_details_group",
        "flat_call",
        "prompts_get",
        "resources_read",
        "facade_invoke",
    ),
}

#: The error types a call path reports, by refusal class (#1785): who refused.
#: A type missing here is reported as `unexpected`, which no check accepts for a
#: listed name.
REFUSAL_CLASS = {
    "ToolAccessDeniedError": "authorization",
    "ToolWithdrawnError": "authorization",
    "AuthorizationDenied": "authorization",
    "NotFound": "authorization",
    "ToolDigestMismatchError": "digest",
    "ApprovalGateError": "approval",
    "RateLimitExceeded": "availability",
    "TenantBudgetExceeded": "availability",
    "NoHealthyMembers": "availability",
    "McpServerNotReady": "availability",
}

# --- the upstreams ----------------------------------------------------------------------


class _Upstream(BaseHTTPRequestHandler):
    """An MCP upstream serving one catalogue, answering every call."""

    catalogue: dict[str, tuple[str, ...]] = {}

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
        answer: dict[str, Any]
        if method == "initialize":
            answer = {
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                    "serverInfo": {"name": "upstream", "version": "0"},
                }
            }
        elif method == "tools/list":
            tools = [{"name": n, "inputSchema": {"type": "object"}} for n in self.catalogue["tool"]]
            answer = {"result": {"tools": tools}}
        elif method == "tools/call":
            answer = {"result": {"content": [{"type": "text", "text": f"ok:{params.get('name')}"}]}}
        elif method == "prompts/list":
            answer = {"result": {"prompts": [{"name": n} for n in self.catalogue["prompt"]]}}
        elif method == "prompts/get" and params.get("name") in self.catalogue["prompt"]:
            text = f"prompt:{params.get('name')}"
            answer = {"result": {"messages": [{"role": "user", "content": {"type": "text", "text": text}}]}}
        elif method == "prompts/get":
            answer = {"error": {"code": -32602, "message": f"Unknown prompt: {params.get('name')}"}}
        elif method == "resources/list":
            answer = {"result": {"resources": [{"uri": u, "name": u} for u in self.catalogue["resource"]]}}
        elif method == "resources/templates/list":
            answer = {"result": {"resourceTemplates": []}}
        elif method == "resources/read" and params.get("uri") in self.catalogue["resource"]:
            answer = {"result": {"contents": [{"uri": params.get("uri", ""), "text": "body"}]}}
        elif method == "resources/read":
            answer = {"error": {"code": -32002, "message": f"Resource not found: {params.get('uri')}"}}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _upstream(catalogue: dict[str, tuple[str, ...]]) -> str:
    handler = type("_ThisUpstream", (_Upstream,), {"catalogue": catalogue})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp"


# --- the generated governance -------------------------------------------------------------


@dataclass(frozen=True)
class Governance:
    """One generation of governance over the fixed topology. Every field is a tuple, so it hashes.

    Attributes:
        server_tools: (server, "allow"|"deny", names): the server's own ``tools:`` policy.
        group_tools: ("allow"|"deny", names) on ``pool``, or None.
        member_tools: (member, "allow"|"deny", names): ``pool.members[].tools``.
        tenant_tools: (scope, tenant, "allow"|"deny", names): ``tool_access.member.<tenant>``.
        access: (scope, kind, "allow"|"deny", names): ``access.<kind>`` for prompts and resources.
        withdrawn: (scope, tenant or "", kind, name): ``tool_projection`` withdrawals.
        pinned: (tenant, member): ``pool.canary.pinned_tenants``.
        drifted: (scope, tool): a block-mode digest pin the upstream does not match.
    """

    server_tools: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    group_tools: tuple[str, tuple[str, ...]] | None = None
    member_tools: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    tenant_tools: tuple[tuple[str, str, str, tuple[str, ...]], ...] = ()
    access: tuple[tuple[str, str, str, tuple[str, ...]], ...] = ()
    withdrawn: tuple[tuple[str, str, str, str], ...] = ()
    pinned: tuple[tuple[str, str], ...] = ()
    drifted: tuple[tuple[str, str], ...] = ()


def _policy(mode: str, names: tuple[str, ...]) -> dict[str, list[str]]:
    return {"allow_list" if mode == "allow" else "deny_list": list(names)}


_WITHDRAWN_KEY = {"tool": "withdrawn", "prompt": "withdrawn_prompts", "resource": "withdrawn_resources"}


def config(topology: str, endpoints: dict[str, str], governance: Governance) -> dict[str, Any]:
    """The whole configuration file for *governance*: the topology never changes, the governance does."""
    specs: dict[str, dict[str, Any]] = {
        server: {"mode": "remote", "endpoint": endpoints[server], "idle_ttl_s": 86400, "health_check_interval_s": 3600}
        for server in SERVERS
    }
    specs[GROUP] = {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "members": [{"id": ALPHA, "priority": 1}, {"id": BETA, "priority": 2}],
    }
    for server, mode, names in governance.server_tools:
        specs[server]["tools"] = _policy(mode, names)
    if governance.group_tools is not None:
        specs[GROUP]["tools"] = _policy(*governance.group_tools)
    for member, mode, names in governance.member_tools:
        for entry in specs[GROUP]["members"]:
            if entry["id"] == member:
                entry["tools"] = _policy(mode, names)
    for scope, tenant, mode, names in governance.tenant_tools:
        specs[scope].setdefault("tool_access", {}).setdefault("member", {})[tenant] = _policy(mode, names)
    for scope, kind, mode, names in governance.access:
        specs[scope].setdefault("access", {})[kind] = _policy(mode, names)
    for scope, tenant, kind, name in governance.withdrawn:
        block = specs[scope].setdefault("tool_projection", {})
        if tenant:
            block = block.setdefault("tenant_overrides", {}).setdefault(tenant, {})
        block.setdefault(_WITHDRAWN_KEY[kind], []).append(name)
    if governance.pinned:
        specs[GROUP]["canary"] = {"pinned_tenants": dict(governance.pinned)}
    for scope, tool in governance.drifted:
        block = specs[scope].setdefault("tool_projection", {})
        block["digest_enforcement"] = "block"
        block.setdefault("pins", {})[tool] = hashlib.sha256(b"not the schema " + tool.encode()).hexdigest()
    return {
        "tool_access": {"mode": topology},
        "logging": {"level": "ERROR"},
        "config_reload": {"enabled": False},
        "rate_limit": {"rps": 100000, "burst": 100000},
        "execution": {"tenant_limits": {t: {"max_concurrency": 100, "rps": 100000, "burst": 100000} for t in TENANTS}},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "memory"},
        },
        "mcp_servers": specs,
    }


# --- driving the served app --------------------------------------------------------------


def _jsonrpc(text: str) -> dict[str, Any]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return dict(json.loads(stripped))
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[len("data: ") :]))
    return {"unparsed": text[:300]}


@dataclass
class Gateway:
    client: Any
    keys: dict[str, str]
    facade: Any
    principals: dict[str, Any]

    def post(self, tenant: str, method: str, params: dict[str, Any], name: str | None = None) -> dict[str, Any]:
        headers = {
            "MCP-Protocol-Version": MODERN_VERSION,
            "Mcp-Method": method,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-API-Key": self.keys[tenant],
        }
        if name is not None:
            headers["Mcp-Name"] = name
        envelope = {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientInfo": {"name": "parity-harness", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": envelope}}
        return _jsonrpc(self.client.post("/mcp", headers=headers, content=json.dumps(body)).text)

    def tool(self, tenant: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.post(tenant, "tools/call", {"name": tool, "arguments": arguments}, name=tool)


def _structured(payload: dict[str, Any]) -> Any:
    """What a management tool returned, structured or as JSON text."""
    result = payload.get("result") or {}
    if result.get("isError"):
        return {"error": _text(result)}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    try:
        return json.loads(_text(result))
    except ValueError:
        return {"error": _text(result) or payload.get("error")}


def _text(result: dict[str, Any]) -> str:
    return "".join(str(block.get("text", "")) for block in result.get("content") or [] if isinstance(block, dict))


def boot(topology: str, workdir: Path) -> tuple[Gateway, Callable[[Governance], dict[str, Any]], Any]:
    """Boot the app for *topology* on an empty governance; return it, a function applying one, and the context."""
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    endpoints = {server: _upstream(CATALOGUES[server]) for server in SERVERS}

    from starlette.testclient import TestClient

    from mcp_hangar.application.commands import StartMcpServerCommand
    from mcp_hangar.application.commands.commands import ReloadConfigurationCommand
    from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
    from mcp_hangar.facade import Hangar, SyncHangar
    from mcp_hangar.logging_config import setup_logging
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, warm_the_front_door_catalogue

    # As the CLI sets it before it boots; debug lines for every call are most of an example's time.
    setup_logging(level="ERROR")
    logging.getLogger().setLevel(logging.ERROR)
    config_file = workdir / "config.yaml"
    config_file.write_text(json.dumps(config(topology, endpoints, Governance())))  # JSON is YAML
    context = bootstrap(config_path=str(config_file))
    warm_the_front_door_catalogue(context.runtime)
    # Started as the front door's warm-up starts them, so `McpServerStarted`
    # fills the catalogue the listings, pins and withdrawals read.
    for server in SERVERS:
        context.runtime.command_bus.send(StartMcpServerCommand(mcp_server_id=server))

    auth = context.auth_components
    keys, principals = {}, {}
    for tenant in TENANTS:
        principal = f"svc:{tenant}"
        keys[tenant] = auth.api_key_store.create_key(principal_id=principal, name=tenant, tenant_id=tenant)
        auth.role_store.assign_role(principal, "developer")
        principals[tenant] = Principal(
            id=PrincipalId(principal), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=tenant, groups=frozenset()
        )

    # The facade over this context, as `test_facade.py` builds one over a pre-initialized context.
    hangar = Hangar(config_path=str(config_file), _context=context)
    hangar._started = True
    facade = SyncHangar(hangar)

    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), context.auth_components)
    client = TestClient(app, base_url=BASE_URL)
    client.__enter__()

    def apply(governance: Governance) -> dict[str, Any]:
        config_file.write_text(json.dumps(config(topology, endpoints, governance)))
        return dict(
            context.runtime.command_bus.send(ReloadConfigurationCommand(config_path=str(config_file), graceful=False))
        )

    return Gateway(client=client, keys=keys, facade=facade, principals=principals), apply, context


# --- what each surface shows and each call path does ---------------------------------------

#: One call's outcome: "ok", or "<refusal class>:<error type>".
Outcome = str
OK = "ok"


def _refused(error_type: str | None) -> Outcome:
    # A group's executor reports an unknown tool as a failed call, a server's
    # validation as a field error, and the facade raises either: one outcome.
    kind = "NotFound" if error_type in (None, "ToolNotFoundError") else error_type
    return f"{REFUSAL_CLASS.get(kind, 'unexpected')}:{kind}"


def _batch_outcome(batch: Any) -> Outcome:
    """The outcome of the one call a `hangar_call` batch made."""
    if not isinstance(batch, dict):
        return _refused(f"unparsed {batch!r}"[:80])
    if batch.get("validation_errors"):
        return _refused("NotFound")
    results = batch.get("results") or []
    if not results:
        return _refused(f"no result: {batch.get('error')}")
    return OK if results[0]["success"] else _refused(results[0]["error_type"])


def _jsonrpc_error(payload: dict[str, Any]) -> tuple[Any, Any] | None:
    """A JSON-RPC answer's error as (code, message), or None when it was served."""
    error = payload.get("error")
    if error is not None:
        return (error.get("code"), error.get("message"))
    result = payload.get("result") or {}
    if result.get("isError"):
        return ("isError", _text(result))
    return None


@dataclass
class Observed:
    """Everything one governance generation shows and does, for every caller. Computed once per example."""

    topology: str
    reload: dict[str, Any]
    #: (tenant) -> the members a group call for it may be routed to, from `route_candidates`.
    candidates: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: (tenant, target, kind) -> names `decide()` allows on every route (on the group: every candidate).
    allowed_on_every_route: dict[tuple[str, str, str], frozenset[str]] = field(default_factory=dict)
    #: (tenant, target, kind) -> names `decide()` allows on at least one route.
    allowed_on_some_route: dict[tuple[str, str, str], frozenset[str]] = field(default_factory=dict)
    facade: dict[tuple[str, str, str], Outcome] = field(default_factory=dict)
    hangar_call: dict[tuple[str, str, str], Outcome] = field(default_factory=dict)
    hangar_tools: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)
    details_tools: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)
    details_withheld: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)
    details_members: dict[str, frozenset[str]] = field(default_factory=dict)
    tools_list: dict[str, frozenset[str]] = field(default_factory=dict)
    discover: dict[str, frozenset[str]] = field(default_factory=dict)
    prompts_list: dict[str, frozenset[str]] = field(default_factory=dict)
    resources_list: dict[str, frozenset[str]] = field(default_factory=dict)
    #: (tenant, name) -> None when served, else the JSON-RPC error (code, message).
    flat_call: dict[tuple[str, str], tuple[Any, Any] | None] = field(default_factory=dict)
    prompts_get: dict[tuple[str, str], tuple[Any, Any] | None] = field(default_factory=dict)
    resources_read: dict[tuple[str, str], tuple[Any, Any] | None] = field(default_factory=dict)

    def single_route(self, tenant: str, target: str) -> bool:
        return target != GROUP or len(self.candidates[tenant]) == 1


def _routed_target(name: str, kind: str) -> str:
    """The target a front-door name routes to: a `pool` member's names go through the group (#857)."""
    return GROUP if name in CATALOGUES[GROUP][kind] else GAMMA


def _projected(uri: str) -> str:
    """A resource's ``hangar://`` URI. One no upstream serves is projected under an id nothing is configured as."""
    owner = GROUP if uri in POOL_RESOURCES else GAMMA if uri in GAMMA_RESOURCES else "nowhere"
    return f"hangar://{owner}/{uri}"


def _owning_groups(server: str) -> tuple[str, ...]:
    return (GROUP,) if server in MEMBERS else ()


def _decisions(observed: Observed) -> None:
    """The authorization oracle: `decide()` for the same caller, target, kind and generation (#1785)."""
    from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
    from mcp_hangar.domain.services import get_tool_access_resolver
    from mcp_hangar.domain.services.access_decision import decide
    from mcp_hangar.domain.value_objects.access import Caller, Target
    from mcp_hangar.server.context import get_context

    group = get_context().get_group(GROUP)
    resolver, registry = get_tool_access_resolver(), get_tool_projection_registry()
    for tenant in TENANTS:
        candidates = tuple(str(m.mcp_server_id) for m in group.route_candidates(tenant))
        observed.candidates[tenant] = candidates
        caller = Caller(tenant_id=tenant)
        for target in TARGETS:
            for kind, names in CATALOGUES[target].items():
                if target == GROUP:
                    routes = [Target.group(GROUP, member_server=m, kind=kind) for m in candidates]  # type: ignore[arg-type]
                else:
                    routes = [Target.server(target, owning_groups=_owning_groups(target), kind=kind)]  # type: ignore[arg-type]
                verdicts = {
                    name: [decide(r, name, caller, resolver=resolver, withdrawals=registry).allowed for r in routes]
                    for name in names
                }
                key = (tenant, target, kind)
                observed.allowed_on_every_route[key] = frozenset(n for n, v in verdicts.items() if v and all(v))
                observed.allowed_on_some_route[key] = frozenset(n for n, v in verdicts.items() if any(v))


def _names(listing: Any, key: str = "tools", field_name: str = "name") -> frozenset[str]:
    if not isinstance(listing, dict) or not isinstance(listing.get(key), list):
        return frozenset({f"<no {key}: {str(listing)[:120]}>"})
    return frozenset(str(entry[field_name]) for entry in listing[key])


def _facade_outcome(gateway: Gateway, tenant: str, target: str, tool: str) -> Outcome:
    from mcp_hangar.domain.exceptions import McpServerNotFoundError, ToolCallFailedError, ToolNotFoundError

    try:
        gateway.facade.invoke(target, tool, {}, principal=gateway.principals[tenant], timeout_s=10.0)
    except ToolCallFailedError as exc:
        return _refused(getattr(exc, "code", None) or type(exc).__name__)
    except (ToolNotFoundError, McpServerNotFoundError):
        return _refused("NotFound")
    return OK


def observe(gateway: Gateway, apply: Callable[[Governance], dict[str, Any]], topology: str, g: Governance) -> Observed:
    observed = Observed(topology=topology, reload=apply(g))
    _decisions(observed)
    for tenant in TENANTS:
        for target in TARGETS:
            for tool in (*CATALOGUES[target]["tool"], UNKNOWN_TOOL):
                observed.facade[(tenant, target, tool)] = _facade_outcome(gateway, tenant, target, tool)
                if topology == EGRESS:
                    arguments = {"calls": [{"mcp_server": target, "tool": tool, "arguments": {}}]}
                    batch = _structured(gateway.tool(tenant, "hangar_call", arguments))
                    observed.hangar_call[(tenant, target, tool)] = _batch_outcome(batch)
            observed.hangar_tools[(tenant, target)] = _names(
                _structured(gateway.tool(tenant, "hangar_tools", {"mcp_server": target}))
            )
            details = _structured(gateway.tool(tenant, "hangar_details", {"mcp_server": target}))
            if target == GROUP:
                observed.details_members[tenant] = _names(details, "members", "id")
            else:
                # `tools` only: `withheld_tools` are operator diagnostics, not invocable entries (#1776).
                observed.details_tools[(tenant, target)] = _names(details) if details.get("tools") else frozenset()
                observed.details_withheld[(tenant, target)] = frozenset(
                    str(w["name"]) for w in details.get("withheld_tools") or []
                )
        if topology != FRONT_DOOR:
            continue
        listing = gateway.post(tenant, "tools/list", {})
        observed.tools_list[tenant] = frozenset(n for n in _names(listing.get("result")) if not n.startswith("hangar_"))
        discovered = gateway.client.get("/server/discover", headers={"X-API-Key": gateway.keys[tenant]}).json()
        observed.discover[tenant] = frozenset(n for n in _names(discovered) if not n.startswith("hangar_"))
        observed.prompts_list[tenant] = _names(gateway.post(tenant, "prompts/list", {}).get("result"), "prompts")
        observed.resources_list[tenant] = _names(
            gateway.post(tenant, "resources/list", {}).get("result"), "resources", "uri"
        )
        for tool in (*POOL_TOOLS, *GAMMA_TOOLS, UNKNOWN_TOOL):
            observed.flat_call[(tenant, tool)] = _jsonrpc_error(gateway.tool(tenant, tool, {}))
        for prompt in (*POOL_PROMPTS, *GAMMA_PROMPTS, UNKNOWN_PROMPT):
            params = {"name": prompt, "arguments": {}}
            observed.prompts_get[(tenant, prompt)] = _jsonrpc_error(
                gateway.post(tenant, "prompts/get", params, name=prompt)
            )
        for uri in (*POOL_RESOURCES, *GAMMA_RESOURCES, UNKNOWN_RESOURCE):
            projected = _projected(uri)
            observed.resources_read[(tenant, uri)] = _jsonrpc_error(
                gateway.post(tenant, "resources/read", {"uri": projected}, name=projected)
            )
    return observed


# --- the invariant, per surface -----------------------------------------------------------


def _served_tools(o: Observed, tenant: str, target: str) -> frozenset[str]:
    """The tools a facade call from *tenant* to *target* is served."""
    return frozenset(n for n in CATALOGUES[target]["tool"] if o.facade[(tenant, target, n)] == OK)


def expected(o: Observed, tenant: str, target: str, kind: str) -> frozenset[str]:
    """What a surface must list for *tenant* on *target*.

    A tool with one route (a server named directly, a pinned tenant): exactly
    what the facade's call is served. With several (a load-balanced tenant on the
    group): what `decide()` allows on every member the call may be routed to
    (decision 1 on #1763). A prompt or a resource: what `decide()` allows on
    every route, since its fetch shares the listing's predicate.

    The front door serves an upstream's prompts and resources only to a tenant
    its flat tool map shows a tool of (`prompt_proxy._upstream_ids`, documented
    there since #1029). Listing and fetch both apply it, so it is fail-closed and
    agrees with itself; it is applied here as the documented rule it is.
    """
    if kind != "tool":
        # Listing and fetch share one predicate, so the fetch is no independent
        # witness: both are held to `decide()` (and the fetch is checked on its own).
        return o.allowed_on_every_route[(tenant, target, kind)] if _upstream_shown(o, tenant, target) else frozenset()
    if o.single_route(tenant, target):
        return _served_tools(o, tenant, target)
    return o.allowed_on_every_route[(tenant, target, kind)]


def _upstream_shown(o: Observed, tenant: str, target: str) -> bool:
    """Whether the front door's flat tool map shows *tenant* a tool of *target* (`prompt_proxy._upstream_ids`)."""
    return bool(o.tools_list[tenant] & set(CATALOGUES[target]["tool"]))


def _why(o: Observed, tenant: str, target: str, kind: str, names: frozenset[str]) -> str:
    """Each name with what a call does and what `decide()` says, so a failure names the refusal class."""
    parts = []
    for name in sorted(names):
        call = o.facade.get((tenant, target, name), "n/a") if kind == "tool" else "n/a"
        every = name in o.allowed_on_every_route[(tenant, target, kind)]
        some = name in o.allowed_on_some_route[(tenant, target, kind)]
        parts.append(f"{name} (facade call: {call}; decide on every route: {every}, on some: {some})")
    return "; ".join(parts)


def _compare(o: Observed, surface: str, tenant: str, target: str, kind: str, listed: frozenset[str]) -> list[str]:
    want = expected(o, tenant, target, kind)
    if listed == want:
        return []
    route = "one route" if o.single_route(tenant, target) else f"routes {o.candidates[tenant]}"
    return [
        f"{surface}: {tenant} on {target} ({kind}, {route}) is shown {sorted(listed)}, may call {sorted(want)}. "
        f"Shown, not callable: {_why(o, tenant, target, kind, listed - want)}. "
        f"Callable, not shown: {_why(o, tenant, target, kind, want - listed)}"
    ]


def _flat_expected(o: Observed, tenant: str, kind: str) -> frozenset[str]:
    return expected(o, tenant, GROUP, kind) | expected(o, tenant, GAMMA, kind)


def _compare_flat(o: Observed, surface: str, tenant: str, kind: str, listed: frozenset[str]) -> list[str]:
    problems = []
    for target in (GROUP, GAMMA):
        names = frozenset(CATALOGUES[target][kind])
        problems += _compare(o, surface, tenant, target, kind, listed & names)
    return problems


def _listings_of(o: Observed, tenant: str, target: str) -> frozenset[str]:
    """Every tool name any surface shows *tenant* for *target*."""
    shown = set(o.hangar_tools.get((tenant, target), ())) | set(o.details_tools.get((tenant, target), ()))
    if o.topology == FRONT_DOOR and target in (GROUP, GAMMA):
        shown |= set(o.tools_list[tenant]) & set(CATALOGUES[target]["tool"])
    return frozenset(shown) & frozenset(CATALOGUES[target]["tool"])


def _call_path(o: Observed, surface: str, outcomes: dict[tuple[str, str, str], Outcome]) -> list[str]:
    """A call path serves every listed name, refuses the unknown one, and serves no name no route allows."""
    problems = []
    for (tenant, target, tool), outcome in sorted(outcomes.items()):
        if tool == UNKNOWN_TOOL:
            if outcome == OK:
                problems.append(f"{surface}: {tenant} calling the unknown {tool} on {target} was served")
            continue
        if outcome != OK and tool in _listings_of(o, tenant, target):
            problems.append(f"{surface}: {tenant} is shown {tool} on {target}, and the call is refused: {outcome}")
        if outcome == OK and tool not in o.allowed_on_some_route[(tenant, target, "tool")]:
            problems.append(f"{surface}: {tenant} calling {tool} on {target} was served, and no route allows it")
        if (
            outcome.startswith("authorization:")
            and o.single_route(tenant, target)
            and tool in o.allowed_on_every_route[(tenant, target, "tool")]
        ):
            problems.append(
                f"{surface}: {tenant} calling {tool} on {target} was refused ({outcome}); decide() allows it"
            )
    return problems


def _indistinguishable(
    surface: str,
    answers: dict[tuple[str, str], tuple[Any, Any] | None],
    unknown: str,
    listed: dict[str, frozenset[str]],
    spelled: Callable[[str], str] = str,
) -> list[str]:
    """A refused name is answered as a name nothing serves (#905), and a shown one is served."""
    problems = []
    for (tenant, name), error in sorted(answers.items(), key=str):
        if name == unknown:
            continue
        if error is None:
            continue
        absent = answers[(tenant, unknown)]
        if absent is None or (error[0], str(error[1]).replace(spelled(name), spelled(unknown))) != absent:
            problems.append(f"{surface}: {tenant} asking for {name} gets {error}; an unknown name gets {absent}")
        if spelled(name) in listed[tenant]:
            problems.append(f"{surface}: {tenant} is shown {name}, and asking for it is refused: {error}")
    return problems


def _fetch_refuses_only_what_is_denied(
    o: Observed, surface: str, kind: str, answers: dict[tuple[str, str], tuple[Any, Any] | None]
) -> list[str]:
    """A fetch with one route refuses a name only when `decide()` refuses it there (authorization class)."""
    problems = []
    for target in (GROUP, GAMMA):
        for tenant in TENANTS:
            if not (o.single_route(tenant, target) and _upstream_shown(o, tenant, target)):
                continue
            for name in o.allowed_on_every_route[(tenant, target, kind)]:
                if answers[(tenant, name)] is not None:
                    problems.append(
                        f"{surface}: {tenant} asking for {name} on {target} is refused {answers[(tenant, name)]}; "
                        f"decide() allows it on the one route {o.candidates[tenant] if target == GROUP else target}"
                    )
    return problems


def _hangar_tools(o: Observed) -> list[str]:
    return [
        problem
        for tenant in TENANTS
        for target in TARGETS
        for problem in _compare(o, "hangar_tools", tenant, target, "tool", o.hangar_tools[(tenant, target)])
    ]


def _hangar_details(o: Observed) -> list[str]:
    problems = []
    for tenant in TENANTS:
        for target in SERVERS:
            problems += _compare(o, "hangar_details", tenant, target, "tool", o.details_tools[(tenant, target)])
            # Diagnostics name only what the caller may list (#905, #1776): authorized, then withheld.
            undisclosable = o.details_withheld[(tenant, target)] - o.allowed_on_every_route[(tenant, target, "tool")]
            if undisclosable:
                problems.append(f"hangar_details: {tenant} is told {target} withholds {sorted(undisclosable)}")
    return problems


def _hangar_details_group(o: Observed) -> list[str]:
    """A group's details disclose to a tenant only the members a call from it may be routed to (#2019)."""
    return [
        f"hangar_details_group: {tenant} is shown members {sorted(o.details_members[tenant])} of {GROUP}; "
        f"a call from it may be routed to {sorted(o.candidates[tenant])} only"
        for tenant in TENANTS
        if not o.details_members[tenant] <= set(o.candidates[tenant])
    ]


def _flat_listing(
    surface: str, kind: str, shown: Callable[[Observed, str], frozenset[str]]
) -> Callable[..., list[str]]:
    def check_listing(o: Observed) -> list[str]:
        return [problem for tenant in TENANTS for problem in _compare_flat(o, surface, tenant, kind, shown(o, tenant))]

    return check_listing


def _upstream_uris(o: Observed, tenant: str) -> frozenset[str]:
    return frozenset(u.split("/", 3)[3] for u in o.resources_list[tenant] if u.startswith("hangar://"))


def _hangar_call(o: Observed) -> list[str]:
    return _call_path(o, "hangar_call", o.hangar_call) + [
        f"hangar_call and the facade disagree: {key} is {outcome} and {o.facade[key]}"
        for key, outcome in sorted(o.hangar_call.items())
        if outcome != o.facade[key]
    ]


def _flat_call(o: Observed) -> list[str]:
    problems = _indistinguishable("flat_call", o.flat_call, UNKNOWN_TOOL, o.tools_list)
    for (tenant, tool), error in sorted(o.flat_call.items(), key=str):
        if tool == UNKNOWN_TOOL:
            continue
        target = _routed_target(tool, "tool")
        via_facade = o.facade[(tenant, target, tool)]
        # A flat call is served only for a name the front door lists, so a
        # load-balanced tenant may be refused a name one candidate denies
        # that the facade's call, routed to another, is served (decision 1).
        may_differ = (
            not o.single_route(tenant, target) and tool not in o.allowed_on_every_route[(tenant, target, "tool")]
        )
        if (error is None) != (via_facade == OK) and not (error is not None and may_differ):
            problems.append(
                f"flat_call: {tenant} calling {tool} is {'served' if error is None else 'refused'}; "
                f"the facade's call of {tool} on {target} is {via_facade}"
            )
    return problems


def _prompts_get(o: Observed) -> list[str]:
    return _indistinguishable("prompts_get", o.prompts_get, UNKNOWN_PROMPT, o.prompts_list) + (
        _fetch_refuses_only_what_is_denied(o, "prompts_get", "prompt", o.prompts_get)
    )


def _resources_read(o: Observed) -> list[str]:
    return _indistinguishable(
        "resources_read", o.resources_read, UNKNOWN_RESOURCE, o.resources_list, _projected
    ) + _fetch_refuses_only_what_is_denied(o, "resources_read", "resource", o.resources_read)


CHECKS: dict[str, Callable[[Observed], list[str]]] = {
    "hangar_tools": _hangar_tools,
    "hangar_details": _hangar_details,
    "hangar_details_group": _hangar_details_group,
    "tools_list": _flat_listing("tools_list", "tool", lambda o, t: o.tools_list[t]),
    "server_discover": _flat_listing("server_discover", "tool", lambda o, t: o.discover[t]),
    "prompts_list": _flat_listing("prompts_list", "prompt", lambda o, t: o.prompts_list[t]),
    "resources_list": _flat_listing("resources_list", "resource", _upstream_uris),
    "hangar_call": _hangar_call,
    "facade_invoke": lambda o: _call_path(o, "facade_invoke", o.facade),
    "flat_call": _flat_call,
    "prompts_get": _prompts_get,
    "resources_read": _resources_read,
}


def check(surface: str, o: Observed) -> list[str]:
    """The invariant on one surface for one observed generation. An empty list is a pass."""
    replaced = [o.reload.get(k) for k in ("mcp_servers_updated", "mcp_servers_added", "mcp_servers_removed")]
    restarted = [f"the governance reload restarted or replaced servers: {o.reload}"] if any(replaced) else []
    return restarted + CHECKS[surface](o)


# --- the generator ------------------------------------------------------------------------


def governances() -> Any:
    """Every kind of governance the tracker review names, over the fixed topology."""
    from hypothesis import strategies as st

    def subset(names: tuple[str, ...]) -> Any:
        return st.lists(st.sampled_from(names), min_size=1, max_size=len(names), unique=True).map(
            lambda chosen: tuple(sorted(chosen))
        )

    mode = st.sampled_from(("allow", "deny"))

    def policy_on(scope: str, kind: str = "tool") -> Any:
        return st.tuples(mode, subset(CATALOGUES[scope][kind]))

    server_tools = st.tuples(*(st.none() | policy_on(s).map(lambda p, s=s: (s, *p)) for s in SERVERS)).map(
        lambda entries: tuple(e for e in entries if e is not None)
    )
    member_tools = st.tuples(*(st.none() | policy_on(m).map(lambda p, m=m: (m, *p)) for m in MEMBERS)).map(
        lambda entries: tuple(e for e in entries if e is not None)
    )
    tenant_tools = st.lists(
        st.sampled_from(TARGETS).flatmap(
            lambda scope: st.tuples(st.just(scope), st.sampled_from(TENANTS), mode, subset(CATALOGUES[scope]["tool"]))
        ),
        max_size=2,
        unique_by=lambda e: (e[0], e[1]),
    ).map(tuple)
    access = st.lists(
        st.tuples(st.sampled_from(TARGETS), st.sampled_from(("prompt", "resource"))).flatmap(
            lambda sk: st.tuples(st.just(sk[0]), st.just(sk[1]), mode, subset(CATALOGUES[sk[0]][sk[1]]))
        ),
        max_size=2,
        unique_by=lambda e: (e[0], e[1]),
    ).map(tuple)
    withdrawn = st.lists(
        st.tuples(
            st.sampled_from(TARGETS), st.sampled_from(("", *TENANTS)), st.sampled_from(("tool", "prompt", "resource"))
        ).flatmap(lambda stk: st.tuples(*map(st.just, stk), st.sampled_from(CATALOGUES[stk[0]][stk[2]]))),
        max_size=3,
        unique=True,
    ).map(tuple)
    pinned = st.lists(
        st.tuples(st.sampled_from(TENANTS[1:]), st.sampled_from(MEMBERS)), max_size=2, unique_by=lambda e: e[0]
    ).map(lambda entries: tuple(sorted(entries)))
    # Digest drift on the standalone server only: a pin a group member drifts
    # from refuses the member a call is routed to, which is availability, not
    # the authorization this test compares.
    drifted = st.lists(st.sampled_from(GAMMA_TOOLS).map(lambda t: (GAMMA, t)), max_size=1).map(tuple)
    return st.builds(
        Governance,
        server_tools=server_tools,
        group_tools=st.none() | policy_on(GROUP),
        member_tools=member_tools,
        tenant_tools=tenant_tools,
        access=access,
        withdrawn=withdrawn,
        pinned=pinned,
        drifted=drifted,
    )


#: Seeds, run first on every budget. The first is the live tier's configuration
#: (`tests/live/test_t0_http_listing_matches_call.py`): `beta` denies `power` as a
#: member, `alpha` withdraws `divide` for the load-balanced tenant, and a tenant
#: is pinned to each member. The second puts a tenant's own policy on the group
#: and on a member, and withdraws a prompt and a resource on one member, where a
#: tenant is pinned to the other: the shapes the pre-#1784 surfaces decided under
#: the wrong scope (D1 to D5 on #1784).
SEEDS = (
    Governance(
        member_tools=((BETA, "deny", ("power",)),),
        withdrawn=((ALPHA, "tenant-lb", "tool", "divide"),),
        pinned=(("tenant-one", ALPHA), ("tenant-two", BETA)),
        drifted=((GAMMA, "purge"),),
    ),
    Governance(
        tenant_tools=((GROUP, "tenant-lb", "deny", ("add",)), (ALPHA, "tenant-one", "deny", ("power",))),
        access=((GROUP, "prompt", "deny", ("audit",)), (GAMMA, "resource", "deny", ("doc://gamma/one",))),
        withdrawn=(
            (ALPHA, "", "prompt", "greet"),
            (ALPHA, "", "resource", "doc://pool/one"),
            (GAMMA, "tenant-two", "tool", "ping"),
        ),
        pinned=(("tenant-one", BETA),),
    ),
)


# --- the runs -----------------------------------------------------------------------------


def run_surface(surface: str, observe_once: Callable[[Governance], Observed], max_examples: int) -> dict[str, Any]:
    """One Hypothesis run of the invariant on *surface*: passed, or its shrunk falsifying example."""
    from hypothesis import HealthCheck, Phase, example, given, settings

    last: dict[str, Any] = {}
    seen = {"examples": 0, "governed": 0}

    def prop(g: Governance) -> None:
        o = observe_once(g)
        seen["examples"] += 1
        if any(o.allowed_on_every_route[k] != frozenset(CATALOGUES[k[1]][k[2]]) for k in o.allowed_on_every_route):
            seen["governed"] += 1
        problems = check(surface, o)
        if problems:
            last.update(governance=repr(g), problems=problems)
            raise AssertionError(problems[0])

    for seed in reversed(SEEDS):
        prop = example(seed)(prop)
    test = settings(
        max_examples=max_examples,
        derandomize=True,
        database=None,
        deadline=None,
        suppress_health_check=list(HealthCheck),
        phases=(Phase.explicit, Phase.generate, Phase.shrink),
        report_multiple_bugs=False,
    )(given(governances())(prop))
    started = time.monotonic()
    try:
        test()
    except Exception as exc:  # noqa: BLE001 -- the report carries it to the parent test
        # Hypothesis replays the shrunk example last, so `last` holds it.
        return {
            "passed": False,
            "falsifying": last.get("governance"),
            "problems": last.get("problems") or [f"{type(exc).__name__}: {exc}", traceback.format_exc()[-3000:]],
            **seen,
            "seconds": round(time.monotonic() - started, 2),
        }
    return {"passed": True, **seen, "seconds": round(time.monotonic() - started, 2)}


def main(topology: str, out: Path) -> None:
    max_examples = int(os.environ.get("HANGAR_PARITY_EXAMPLES", "20"))
    started = time.monotonic()
    gateway, apply, _context = boot(topology, out.parent)
    booted = time.monotonic() - started

    cache: dict[Governance, Observed] = {}

    def observe_once(g: Governance) -> Observed:
        if g not in cache:
            cache[g] = observe(gateway, apply, topology, g)
        return cache[g]

    import mcp_hangar

    surfaces = {surface: run_surface(surface, observe_once, max_examples) for surface in SURFACES[topology]}
    out.write_text(
        json.dumps(
            {
                "hangar": mcp_hangar.__file__,
                "max_examples": max_examples,
                "boot_seconds": round(booted, 2),
                "generations_observed": len(cache),
                "surfaces": surfaces,
            }
        )
    )
    sys.stdout.flush()
    sys.stderr.flush()
    # The upstreams and the client's portal run on daemon threads; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
