"""Drive the facade's ``invoke`` or a stdio session, recording which security predicates ran (#1786).

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

The file configures what the in-process probes configure by hand: one remote
upstream, one payload-size validator, a budget for ``tenant-a``, a digest pin that
does not match under ``warn``, and an allow-all L7 policy (set on the server, as
``POST /api/mcp_servers/{id}/l7_policy`` sets it). None of them refuses the call;
each is evaluated.

Only the call is recorded: the spy is installed before the boot, so every thread
inherits it, and records between the request and its answer. ``--mutate`` wraps
``_policy_merge.is_allowed`` so it is asked with the server id in the tenant
slot; the gate must notice.
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
SURFACES = ("facade", "stdio", "stdio_front_door")


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
    }


def _allow_all_l7(runtime: Any) -> None:
    from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolAction

    runtime.repository.get(UPSTREAM).set_l7_policy(L7Policy(default_action=ToolAction.ALLOW))


def _mutate() -> None:
    """Ask the tool-access decision with the server id where the tenant goes."""
    from mcp_hangar.domain.services import _policy_merge

    original = _policy_merge.is_allowed

    def server_in_the_tenant_slot(resolver, mcp_server_id, name, **kwargs):  # type: ignore[no-untyped-def]
        return original(resolver, mcp_server_id, name, **{**kwargs, "member_id": mcp_server_id})

    _policy_merge.is_allowed = server_in_the_tenant_slot


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


def main(surface: str, out: Path, mutate: bool) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    import mcp_hangar
    from tests.unit._predicate_spy import Spy, to_json
    from tests.unit._security_predicates import PREDICATES
    from tests.unit.test_security_predicates_are_reachable import _upstream

    if mutate:
        _mutate()
    spy = Spy(PREDICATES)
    with _upstream() as endpoint, spy.installed():
        config_file = out.parent / "config.yaml"
        config_file.write_text(json.dumps(_config(surface, endpoint)))  # JSON is YAML
        outcome = _facade(config_file, spy) if surface == "facade" else _stdio(config_file, surface, spy)

    calls, scopes = outcome.pop("recorded")
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, **outcome, **to_json(calls, scopes)}))
    sys.stderr.flush()
    # Worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in SURFACES:
        raise SystemExit(f"usage: {sys.argv[0]} <{'|'.join(SURFACES)}> <out.json> [--mutate]")
    main(sys.argv[1], Path(sys.argv[2]), "--mutate" in sys.argv[3:])
