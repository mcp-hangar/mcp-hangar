"""Boot Hangar from a file, reload it, and probe the served app after each reload (#1424).

Run as a script, in its own interpreter, by ``test_a_reload_is_served_whole.py``:
``python _reload_served_harness.py <front_door|egress> <workdir> <out.json>``.
Not collected by pytest. A separate process because ``bootstrap()`` fills
process-global state -- the runtime, the resolver, the executor, the guard --
and two boots in one interpreter would read each other's.

What runs is production: ``bootstrap(config_path=...)``, then the app
``serve --http`` serves, composed the way ``ServerLifecycle.run_http`` composes
it -- ``/api`` beside ``/mcp``, behind the authentication layer when auth is on
-- under starlette's ``TestClient``, over streamable HTTP. The reloads go
through ``POST /api/config/reload``, through the SIGHUP handler ``serve``
installs (a real signal to this process), and through ``ConfigReloadWorker``.

* ``front_door``: auth on, an API key issued WITHOUT a tenant. Before and after
  every reload it lists no tools and its call answers ``-32601``. A file that
  switches to ``egress`` is refused. ``ui_resources``, ``resource_links``,
  ``headers.param_validation`` and ``interceptors`` (on a flat call, which runs
  them since #1425) are edited and then deleted by reload, each read off the
  served surface.
* ``egress``: ``interceptors`` through ``hangar_call``, and ``execution``
  through ``POST /api/config/export``, which reads the live concurrency manager.

One thing is injected, and only in ``front_door``: the SDK's pre-dispatch
tool listing is made to fail for one call, which is the condition
``headers.param_validation.required`` exists to refuse. Everything else is the
shipped code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

import yaml

TESTS = Path(__file__).resolve().parents[1]
UPSTREAM = Path(__file__).with_name("_reload_upstream.py")
MOCK_PROVIDER = TESTS / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "reload-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
TENANT = "tenant:a"

#: front_door: booted, then edited by one reload, then deleted by the next.
FRONT_DOOR_SECTIONS: dict[str, dict[str, Any]] = {
    "boot": {
        "ui_resources": {"tenants": {TENANT: {"allowlist": ["ui://none/"]}}},
        "resource_links": {"max_per_tenant": 2},
        "headers": {"param_validation": {"required": False}},
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": 4096}]},
    },
    "edited": {
        "ui_resources": {"tenants": {TENANT: {"allowlist": ["ui://panels/"]}}},
        "resource_links": {"max_per_tenant": 1},
        "headers": {"param_validation": {"required": True}},
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": 64}]},
    },
    "deleted": {},
}

#: egress: the same, for the two sections this scenario reads.
EGRESS_SECTIONS: dict[str, dict[str, Any]] = {
    "boot": {
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": 4096}]},
        "execution": {"max_concurrency": 7, "default_mcp_server_concurrency": 3},
    },
    "edited": {
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": 256}]},
        "execution": {"max_concurrency": 2, "default_mcp_server_concurrency": 1},
    },
    "deleted": {},
}


def _payload(response: Any) -> dict[str, Any]:
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return dict(json.loads(text))


def _write(path: Path, config: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _served_app(context: Any, lifecycle: Any) -> Any:
    """The app `run_http` serves: `/api` beside `/mcp`, behind auth when auth is on."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    mcp_app = mcp_app_for_serving(context.mcp_server)
    aux = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=context.auth_components))])

    async def combined(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api"):
            await aux(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    auth = context.auth_components
    return lifecycle._create_auth_app(combined, auth) if auth is not None and auth.enabled else combined


class _Reloads:
    """Counts the reloads that finished, either way, to wait on the asynchronous triggers."""

    def __init__(self, event_bus: Any) -> None:
        from mcp_hangar.domain.contracts.event_bus import HandlerKind
        from mcp_hangar.domain.events import ConfigurationReloaded, ConfigurationReloadFailed

        self.finished: list[Any] = []
        for event in (ConfigurationReloaded, ConfigurationReloadFailed):
            event_bus.subscribe(event, self.finished.append, kind=HandlerKind.LOCAL_VIEW)

    def wait_for(self, count: int) -> None:
        deadline = time.monotonic() + 20
        while len(self.finished) < count:
            if time.monotonic() > deadline:
                raise TimeoutError(f"waited for reload #{count}, saw {len(self.finished)}")
            time.sleep(0.05)
        last = self.finished[-1]
        if type(last).__name__ != "ConfigurationReloaded":
            raise AssertionError(f"the reload failed: {last}")


class _Client:
    def __init__(self, client: Any, keys: dict[str, str]) -> None:
        self.client = client
        self.keys = keys

    def rpc(self, who: str | None, method: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MODERN,
            "Mcp-Method": method,
        }
        if who is not None:
            headers["X-API-Key"] = self.keys[who]
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": ENVELOPE}}
        return _payload(self.client.post("/mcp", headers=headers, content=json.dumps(body)))

    def reload(self, who: str | None) -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if who is not None:
            headers["X-API-Key"] = self.keys[who]
        response = self.client.post("/api/config/reload", headers=headers, content="{}")
        return response.status_code, response.json()


# --------------------------------------------------------------------------
# front_door
# --------------------------------------------------------------------------


def _front_door_config(workdir: Path, *, mode: str, sections: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_access": {"mode": mode},
        # The harness runs the watcher itself, with a short interval.
        "config_reload": {"enabled": False},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "sqlite", "path": str(workdir / "auth.db")},
            "role_assignments": [
                {"principal": "group:svc-callers", "role": "service-account", "scope": "global"},
                {"principal": "group:admins", "role": "admin", "scope": "global"},
            ],
        },
        "mcp_servers": {"stub": {"mode": "subprocess", "command": [sys.executable, str(UPSTREAM)], "idle_ttl_s": 600}},
        **sections,
    }


def _seed_keys(db: Path) -> dict[str, str]:
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(db)
    store.initialize()
    try:
        return {
            "tenant": store.create_key(
                principal_id="svc:tenant-a", name="k-a", tenant_id=TENANT, groups=frozenset({"svc-callers"})
            ),
            # Authenticated and holding tool:invoke, with NO tenant: the caller
            # the front door's deny-all rule exists for.
            "no_tenant": store.create_key(principal_id="svc:no-tenant", name="k-nt", groups=frozenset({"svc-callers"})),
            "admin": store.create_key(principal_id="ops:admin", name="k-admin", groups=frozenset({"admins"})),
        }
    finally:
        store.close()


class _ListingFault:
    """Fail the next pre-dispatch tool listing, once: the case ``param_validation.required`` refuses."""

    def __init__(self) -> None:
        from mcp_hangar.fastmcp_server import flat_tool_projection

        self.armed = False
        self.fired = False
        original = flat_tool_projection.generate_projection

        def generate(tenant_id: str | None) -> Any:
            if self.armed:
                self.armed = False
                self.fired = True
                raise RuntimeError("injected: this listing fails")
            return original(tenant_id)

        flat_tool_projection.generate_projection = generate  # type: ignore[assignment]


class _FrontDoor(_Client):
    def __init__(self, client: Any, keys: dict[str, str], fault: _ListingFault) -> None:
        super().__init__(client, keys)
        self.fault = fault
        self.echo = ""
        self.links = ""

    def names(self, who: str) -> list[str]:
        return sorted(tool["name"] for tool in self.rpc(who, "tools/list", {})["result"]["tools"])

    def warm(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            names = self.names("tenant")
            self.echo = next((n for n in names if n.endswith("echo")), "")
            self.links = next((n for n in names if n.endswith("links")), "")
            if self.echo and self.links:
                return
            time.sleep(0.3)
        raise TimeoutError(f"the tenant never saw the upstream's tools: {self.names('tenant')}")

    def call(self, who: str, name: str, arguments: dict[str, Any]) -> Any:
        payload = self.rpc(who, "tools/call", {"name": name, "arguments": arguments})
        if "error" in payload:
            return payload["error"]["code"]
        return "error-result" if payload["result"].get("isError") else "served"

    def probe(self) -> dict[str, Any]:
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        return {
            "mode": get_tool_access_resolver().topology_mode,
            "no_tenant_tools": self.names("no_tenant"),
            "no_tenant_call": self.call("no_tenant", self.echo, {"text": "hi"}),
            "tenant_sees_echo": self.echo in self.names("tenant"),
            "tenant_call": self.call("tenant", self.echo, {"text": "hi"}),
        }

    def sections(self) -> dict[str, Any]:
        self.call("tenant", self.links, {})  # hands out note://1..3
        uris = [r["uri"] for r in self.rpc("tenant", "resources/list", {})["result"]["resources"]]
        self.fault.armed, self.fault.fired = True, False
        unvalidated = self.call("tenant", self.echo, {"text": "hi"})
        fired, self.fault.armed = self.fault.fired, False
        return {
            # 300 bytes of argument: under the booted cap, over the edited one.
            "oversized_flat_call": self.call("tenant", self.echo, {"text": "x" * 300}),
            "ui_listed": "hangar://stub/ui://panels/main" in uris,
            "links": sorted(u.rsplit("/", 1)[-1] for u in uris if "note://" in u and not u.endswith("plain")),
            "unvalidated_call": unvalidated,
            "listing_failed": fired,
        }


def front_door(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.gc import ConfigReloadWorker
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import _setup_signal_handlers, ServerLifecycle, warm_the_front_door_catalogue

    keys = _seed_keys(workdir / "auth.db")
    path = workdir / "config.yaml"

    def configure(*, mode: str = "front_door", sections: str) -> None:
        _write(path, _front_door_config(workdir, mode=mode, sections=FRONT_DOOR_SECTIONS[sections]))

    configure(sections="boot")
    context = bootstrap(config_path=str(path))
    lifecycle = ServerLifecycle(context)
    _setup_signal_handlers(lifecycle)
    reloads = _Reloads(context.runtime.event_bus)
    warm_the_front_door_catalogue(context.runtime)
    fault = _ListingFault()
    out: dict[str, Any] = {"phases": {}, "sections": {}}

    with TestClient(_served_app(context, lifecycle), base_url=BASE_URL) as client:
        fd = _FrontDoor(client, keys, fault)
        fd.warm()
        out["phases"]["boot"] = fd.probe()
        out["sections"]["boot"] = fd.sections()

        status, _body = fd.reload("admin")
        out["phases"]["rest"] = {**fd.probe(), "status": status}

        os.kill(os.getpid(), signal.SIGHUP)
        reloads.wait_for(2)
        out["phases"]["sighup"] = fd.probe()

        worker = ConfigReloadWorker(str(path), context.runtime.command_bus, interval_s=1, use_watchdog=False)
        worker.interval_s = 0.1  # type: ignore[assignment]  # the poll, not the behaviour, is shortened
        worker.start()
        stamp = path.stat().st_mtime + 5
        os.utime(path, (stamp, stamp))  # a byte-identical rewrite, as an editor's save is
        reloads.wait_for(3)
        worker.running = False
        out["phases"]["watcher"] = fd.probe()

        repository = context.runtime.repository
        servers_before = {sid: id(server) for sid, server in repository.get_all().items()}
        configure(mode="egress", sections="boot")
        status, body = fd.reload("admin")
        out["refused"] = {
            **fd.probe(),
            "status": status,
            "message": json.dumps(body),
            "servers_unchanged": servers_before == {sid: id(s) for sid, s in repository.get_all().items()},
        }

        configure(sections="edited")
        fd.reload("admin")
        out["sections"]["edited"] = fd.sections()

        configure(sections="deleted")
        fd.reload("admin")
        out["sections"]["deleted"] = fd.sections()
        out["phases"]["deleted"] = fd.probe()

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    return out


# --------------------------------------------------------------------------
# egress
# --------------------------------------------------------------------------


class _Egress(_Client):
    def hangar_call(self, arguments: dict[str, Any]) -> tuple[bool, str | None]:
        calls = [{"mcp_server": "math", "tool": "add", "arguments": arguments}]
        payload = self.rpc(None, "tools/call", {"name": "hangar_call", "arguments": {"calls": calls}})
        (result,) = json.loads(payload["result"]["content"][0]["text"])["results"]
        return result["success"], result.get("error_type")

    def probe(self) -> dict[str, Any]:
        exported = self.client.post("/api/config/export", headers={"Content-Type": "application/json"})
        return {
            "small": self.hangar_call({"a": 1, "b": 2}),
            "oversized": self.hangar_call({"a": 1, "b": 2, "pad": "x" * 2048}),
            "execution": yaml.safe_load(exported.json()["yaml"]).get("execution"),
        }


def egress(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    path = workdir / "config.yaml"

    def configure(sections: str) -> None:
        _write(
            path,
            {
                "config_reload": {"enabled": False},
                "mcp_servers": {"math": {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}},
                **EGRESS_SECTIONS[sections],
            },
        )

    configure("boot")
    context = bootstrap(config_path=str(path))
    out: dict[str, Any] = {}
    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _Egress(client, {})
        out["boot"] = gateway.probe()
        for phase in ("edited", "deleted"):
            configure(phase)
            status, _body = gateway.reload(None)
            out[phase] = {**gateway.probe(), "status": status}

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    return out


def main(scenario: str, workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    result = {"front_door": front_door, "egress": egress}[scenario](workdir)
    out.write_text(json.dumps(result))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
