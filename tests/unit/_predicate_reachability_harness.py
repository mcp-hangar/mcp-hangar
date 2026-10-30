"""Drive the facade's ``invoke``, a stdio session or the REST API, recording which security predicates ran (#1786).

Run as a script, in its own interpreter, by ``test_security_predicates_are_reachable.py``:
``python _predicate_reachability_harness.py <surface> <out.json> [--mutate]``. Not
collected by pytest.

A separate process for the reason ``tests/integration/_route_decisions_harness.py``
gives: ``bootstrap()`` fills process-global state, and a second bootstrap in one
interpreter would read the first's. These two surfaces are only served by a
process that bootstrapped itself, so each gets its own.

What runs is production, and no gate is patched:

* ``facade``: ``Hangar.from_config`` boots ``bootstrap()`` from a file and
  ``Hangar.invoke`` makes one call, as an embedder's caller in ``tenant-a``.
* ``stdio`` and ``stdio_front_door``: ``run_server``, the function
  ``mcp-hangar serve`` runs, serves stdio over a pair of pipes this script
  holds the other ends of. The file declares the stdio principal (ADR-026) in
  ``tenant-a``. A client thread makes the handshake, then one call: ``hangar_call``
  on egress, the flat ``tools/call`` on the front door. It then closes the pipe,
  which ends the session the way a client exiting ends it.
* ``rest``: ``bootstrap()`` boots from the file and every probe in
  ``_rest_route_probes.ROUTE_PROBES`` is sent, in order, as one request each, to the
  app ``run_http`` assembles. It is the one surface that reports many paths. The
  probes register and remove servers, groups, sources, keys and roles, and reload
  the file, so a process of their own is also what keeps that from the next test.

The file configures what the in-process probes configure by hand: one remote
upstream, one payload-size validator, a budget for ``tenant-a``, a digest pin that
does not match under ``warn``, and an allow-all L7 policy (set on the server, as
``POST /api/mcp_servers/{id}/l7_policy`` sets it). None of them refuses the call;
each is evaluated.

Only the call is recorded: the spy is installed before the boot, so every thread
inherits it, and records between the request and its answer. ``--mutate`` wraps
``_policy_merge.is_allowed`` so it is asked with the server id in the tenant
slot; the gate must notice. On ``rest`` it reports a confined grant's reach as the
server id instead, and sends only the probes a confined caller makes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

# Run as a script from tests/unit: the repo root makes `tests.unit` importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

UPSTREAM = "upstream"
TENANT = "tenant-a"
PRINCIPAL = "svc:caller"
TENANT_PRINCIPAL = "svc:tenant-caller"
SURFACES = ("facade", "stdio", "stdio_front_door", "rest")


def _config(surface: str, endpoint: str) -> dict[str, Any]:
    stdio = surface.startswith("stdio")
    auth: dict[str, Any] = {
        "enabled": True,
        "allow_anonymous": False,
        "api_key": {"enabled": True},
        "storage": {"driver": "memory"},
        "role_assignments": [{"principal": PRINCIPAL, "role": "admin", "scope": "global"}],
    }
    if stdio:
        auth["stdio"] = {"principal": {"id": PRINCIPAL, "tenant_id": TENANT, "roles": ["admin"]}}
    if surface == "rest":
        # The same role, held only within the tenant, as the in-process probes hold it.
        auth["role_assignments"].append({"principal": TENANT_PRINCIPAL, "role": "admin", "scope": f"tenant:{TENANT}"})
    extra: dict[str, Any] = {}
    if surface == "rest":
        # Discovery, to serve its routes. SQLite, because the peer-key name
        # record and the stored tool-access policies need a durable backend.
        extra = {
            "discovery": {"enabled": True, "auto_register": False},
            "persistence": {"backend": "sqlite", "sqlite": {"data_dir": "data"}},
        }
    return {
        "tool_access": {"mode": "front_door" if surface == "stdio_front_door" else "egress"},
        "mcp_servers": {
            UPSTREAM: {
                "mode": "remote",
                "endpoint": endpoint,
                # A pin that does not match, under `warn`: the digest check runs
                # and the call still goes through to the gates after it.
                "tool_projection": {"pins": {"add": "0" * 64}, "digest_enforcement": "warn"},
            }
        },
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": 1_000_000}]},
        "execution": {"tenant_limits": {TENANT: {"max_concurrency": 100, "rps": 1000, "burst": 1000}}},
        "auth": auth,
        "config_reload": {"enabled": False},
        "observability": {"tracing": {"enabled": False}},
        **extra,
    }


def _allow_all_l7(runtime: Any) -> None:
    from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolAction

    runtime.repository.get(UPSTREAM).set_l7_policy(L7Policy(default_action=ToolAction.ALLOW))


def _mutate(surface: str) -> None:
    """Ask the tool-access decision with the server id where the tenant goes."""
    if surface == "rest":
        _mutate_rest()
        return
    from mcp_hangar.domain.services import _policy_merge

    original = _policy_merge.is_allowed

    def server_in_the_tenant_slot(resolver, mcp_server_id, name, **kwargs):  # type: ignore[no-untyped-def]
        return original(resolver, mcp_server_id, name, **{**kwargs, "member_id": mcp_server_id})

    _policy_merge.is_allowed = server_in_the_tenant_slot


def _mutate_rest() -> None:
    """Report a confined grant as reaching the server id, not the caller's tenant."""
    from mcp_hangar.approvals.commands.resolve import ResolveApprovalHandler
    from mcp_hangar.domain.contracts.authorization import GrantScope

    original = ResolveApprovalHandler._authorize

    def server_as_the_tenant(self: Any, principal: Any, approval_id: str) -> GrantScope:
        grant = original(self, principal, approval_id)
        return GrantScope(confined=True, tenant_id=UPSTREAM) if grant.confined else grant

    ResolveApprovalHandler._authorize = server_as_the_tenant  # type: ignore[method-assign]


# --- the facade ------------------------------------------------------------------


def _facade(config_file: Path, spy: Any) -> dict[str, Any]:
    from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType
    from mcp_hangar.facade import Hangar
    from mcp_hangar.server.context import get_context

    async def run() -> dict[str, Any]:
        hangar = Hangar.from_config(config_file)
        await hangar.start()
        try:
            _allow_all_l7(get_context().runtime)
            # Started first, as the in-process probes start theirs, so the
            # catalogue the pin and withdrawal checks read is populated.
            await hangar.start_mcp_server(UPSTREAM)
            caller = Principal(id=PrincipalId(PRINCIPAL), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=TENANT)
            with spy.recording() as (calls, scopes):
                try:
                    result = await hangar.invoke(UPSTREAM, "add", {"a": 1}, principal=caller)
                    served, detail = True, json.dumps(result, default=str)[:300]
                except Exception as exc:  # noqa: BLE001 -- reported, so the test says why the probe was refused
                    served, detail = False, f"{type(exc).__name__}: {exc}"
            return {"served": served, "detail": detail, "recorded": (calls, scopes)}
        finally:
            await hangar.stop()

    return asyncio.run(run())


# --- stdio -----------------------------------------------------------------------


class _Pipe:
    """The client end of a stdio session: newline-delimited JSON-RPC."""

    def __init__(self, to_server: int, from_server: int) -> None:
        self._out = os.fdopen(to_server, "w", encoding="utf-8")
        self._in = os.fdopen(from_server, "r", encoding="utf-8")

    def send(self, message: dict[str, Any]) -> None:
        self._out.write(json.dumps(message) + "\n")
        self._out.flush()

    def answer(self, request_id: int) -> dict[str, Any]:
        """The response to *request_id*, skipping notifications and log lines the server sends first."""
        for line in self._in:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        return {"error": {"message": "the session ended before an answer"}}

    def close(self) -> None:
        self._out.close()


def _stdio_client(pipe: _Pipe, surface: str, spy: Any, outcome: dict[str, Any]) -> None:
    try:
        pipe.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "reachability-probe", "version": "0"},
                },
            }
        )
        pipe.answer(1)
        pipe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        if surface == "stdio_front_door":
            params: dict[str, Any] = {"name": "add", "arguments": {"a": 1}}
        else:
            calls = [{"mcp_server": UPSTREAM, "tool": "add", "arguments": {"a": 1}}]
            params = {"name": "hangar_call", "arguments": {"calls": calls}}
        with spy.recording() as (calls_seen, scopes):
            pipe.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
            answer = pipe.answer(2)
        result = answer.get("result")
        outcome["served"] = isinstance(result, dict) and not result.get("isError")
        outcome["detail"] = json.dumps(answer)[:300]
        outcome["recorded"] = (calls_seen, scopes)
    finally:
        pipe.close()


def _stdio(config_file: Path, surface: str, spy: Any) -> dict[str, Any]:
    from mcp_hangar.server.cli.cli_compat import CLIConfig
    from mcp_hangar.server.context import get_context
    from mcp_hangar.server.lifecycle import ServerLifecycle, run_server

    # The server's stdin and stdout become pipes; the client thread holds the
    # other ends. stderr stays, for the server's logs.
    server_in, to_server = os.pipe()
    from_server, server_out = os.pipe()
    os.dup2(server_in, 0)
    os.dup2(server_out, 1)
    os.close(server_in)
    os.close(server_out)

    # After the boot and before the session: the operator's L7 policy, and the
    # first call's cold start made ahead of it.
    original_start = ServerLifecycle.start

    def start(self: ServerLifecycle) -> None:
        original_start(self)
        from mcp_hangar.application.commands import StartMcpServerCommand

        runtime = get_context().runtime
        _allow_all_l7(runtime)
        runtime.command_bus.send(StartMcpServerCommand(mcp_server_id=UPSTREAM))

    ServerLifecycle.start = start  # type: ignore[method-assign]

    outcome: dict[str, Any] = {"served": False, "detail": "the client never ran", "recorded": (set(), set())}
    client = threading.Thread(
        target=_stdio_client, args=(_Pipe(to_server, from_server), surface, spy, outcome), daemon=True
    )
    client.start()
    run_server(
        CLIConfig(
            http_mode=False,
            http_host="127.0.0.1",
            http_port=0,
            config_path=str(config_file),
            log_file=None,
            log_level="WARNING",
            json_logs=True,
        )
    )
    client.join(timeout=30)
    return outcome


# --- the REST API ----------------------------------------------------------------


def _served_app(context: Any) -> Any:
    """The app `run_http` serves, as far as a REST request sees it: `/api` beside the MCP app, behind auth."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    components = context.auth_components
    mcp_app = mcp_app_for_serving(context.mcp_server)
    aux_app = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))])

    async def combined_app(scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope["type"] in ("http", "websocket") and (path == "/api" or path.startswith("/api/")):
            await aux_app(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    return create_auth_enforced_app(combined_app, components)


def _write_discoverable(directory: Path, names: tuple[str, ...]) -> None:
    """A definition for each name, for the registered source's scan to find."""
    directory.mkdir()
    for name in names:
        definition = {
            "name": name,
            "mode": "http",
            "connection": {"host": "mcp.example.test", "port": 8080},
            "metadata": {"owner": "probes"},
        }
        (directory / f"{name}.yaml").write_text(json.dumps(definition))  # JSON is YAML


def _pending_approval(approval_id: str) -> Any:
    """A held call awaiting a decision, raised in the caller's tenant."""
    from datetime import UTC, datetime, timedelta

    from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState

    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id=approval_id,
        provider_id=UPSTREAM,
        tool_name="add",
        arguments={"a": 1},
        arguments_hash="0" * 64,
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
        state=ApprovalState.PENDING,
        channel="default",
        requested_by=PRINCIPAL,
        tenant_id=TENANT,
    )


def _rest(config_file: Path, spy: Any, mutate: bool) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.context import get_context
    from tests.unit._predicate_spy import to_json
    from tests.unit._rest_route_probes import APPROVAL, DISCOVERY_DIR, ROUTE_PROBES, TO_APPROVE, TO_REJECT
    from tests.unit.test_security_predicates_are_reachable import _BASE_URL, _PEER

    _write_discoverable(config_file.parent / DISCOVERY_DIR, (TO_APPROVE, TO_REJECT))
    context = bootstrap(config_path=str(config_file))
    _allow_all_l7(context.runtime)
    store = context.auth_components.api_key_store
    keys = {
        "fleet": store.create_key(principal_id=PRINCIPAL, name="probe", tenant_id=TENANT),
        "tenant": store.create_key(principal_id=TENANT_PRINCIPAL, name="tenant-probe", tenant_id=TENANT),
    }
    outcomes: dict[str, Any] = {}
    kept: dict[str, str] = {}
    app = _served_app(context)
    with TestClient(app, base_url=_BASE_URL, client=_PEER, raise_server_exceptions=False) as client:

        def send(sent: Any) -> Any:
            headers = {"X-API-Key": keys[sent.caller]}
            return client.request(sent.method, "/api" + sent.path.format_map(kept), headers=headers, json=sent.body)

        client.portal.call(get_context().approval_gate._repository.save, _pending_approval(APPROVAL))
        for probe in ROUTE_PROBES:
            if mutate and probe.caller != "tenant":
                continue
            refused = [(step.name, answer.text) for step in probe.first if (answer := send(step)).status_code >= 300]
            with spy.recording() as (calls, scopes):
                response = send(probe)
            served = 200 <= response.status_code < 300 and not refused
            for name in probe.keeps if served else ():
                kept[name] = response.json()[name]
            detail = f"{response.status_code} {response.text[:300]}" + (f" after {refused}" if refused else "")
            outcomes[probe.name] = {"served": served, "detail": detail, **to_json(calls, scopes)}
    return {"routes": outcomes}


def main(surface: str, out: Path, mutate: bool) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    import mcp_hangar
    from tests.unit._predicate_spy import Spy, to_json
    from tests.unit._security_predicates import PREDICATES
    from tests.unit.test_security_predicates_are_reachable import _upstream

    if mutate:
        _mutate(surface)
    spy = Spy(PREDICATES)
    with _upstream() as endpoint, spy.installed():
        config_file = out.parent / "config.yaml"
        config_file.write_text(json.dumps(_config(surface, endpoint)))  # JSON is YAML
        if surface == "rest":
            outcome = _rest(config_file, spy, mutate)
        elif surface == "facade":
            outcome = _facade(config_file, spy)
        else:
            outcome = _stdio(config_file, surface, spy)

    recorded = to_json(*outcome.pop("recorded")) if "recorded" in outcome else {}
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, **outcome, **recorded}))
    sys.stderr.flush()
    # Worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in SURFACES:
        raise SystemExit(f"usage: {sys.argv[0]} <{'|'.join(SURFACES)}> <out.json> [--mutate]")
    main(sys.argv[1], Path(sys.argv[2]), "--mutate" in sys.argv[3:])
