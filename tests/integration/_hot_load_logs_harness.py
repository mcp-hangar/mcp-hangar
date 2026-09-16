"""Hot-load a server, read its log through the API, then unload and delete (#1506).

Run as a script, in its own interpreter, by
``test_a_hot_loaded_server_has_its_log.py``:
``python _hot_load_logs_harness.py <workdir> <out.json>``. Not collected by
pytest. A separate process for the reason ``_reload_served_harness.py`` gives:
``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, the load goes through the real ``hangar_load`` MCP tool over streamable
HTTP, the unload through ``hangar_unload``, the delete through
``DELETE /api/mcp_servers/{id}``, and every log is read back through
``GET /api/mcp_servers/{id}/logs``. The loaded server is a real stdio
subprocess and writes one line of its own to stderr as it starts
(``MOCK_STDERR_BANNER``); the gateway's stderr reader is what puts that line in
the buffer.

Only the registry and the installer are stood in for, because they are the
network: the fake pair resolves one package and "installs" it as the mock
provider's command. Everything downstream -- the McpServer aggregate, the
runtime store, the buffer registry, the endpoint -- is the real one, and the
handler under test is built exactly as ``server/bootstrap/hot_loading.py``
builds it, including the ``LogBuffers`` port.

``kept`` is declared in the file and is the delete case: ``hangar_unload``
refuses a configured server, and ``DeleteMcpServerHandler`` works on the
repository, where a hot-loaded server never is.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any

from _added_server_logs_harness import _LogGateway
from _reload_keeps_harness import _server, MOCK_PROVIDER
from _reload_served_harness import _served_app, _write, BASE_URL

#: What each server writes to its own stderr at startup.
BANNERS = {"kept": "kept is up", "hot-one": "hot-one is up", "hot-two": "hot-two is up"}


def _config() -> dict[str, Any]:
    return {
        "config_reload": {"enabled": False},
        "mcp_servers": {"kept": _server(env={"MOCK_STDERR_BANNER": BANNERS["kept"]})},
    }


# ---------------------------------------------------------------------------
# The network, stood in for
# ---------------------------------------------------------------------------


@dataclass
class _FakeRegistry:
    """Answers for any name in BANNERS, so `hangar_load` resolves without a registry."""

    async def get_server(self, server_id: str) -> Any:
        from mcp_hangar.domain.contracts.registry import PackageInfo, ServerDetails, TransportInfo

        if server_id not in BANNERS:
            return None
        return ServerDetails(
            id=server_id,
            name=server_id,
            description="a hot-loaded server",
            vendor=None,
            source_url=None,
            is_official=True,
            packages=[
                PackageInfo(
                    registry_type="pypi",
                    identifier=server_id,
                    version=None,
                    transport=TransportInfo(type="stdio"),
                )
            ],
            required_env_vars=[],
        )

    async def search(self, query: str, limit: int = 10) -> list[Any]:
        return []


@dataclass
class _FakeInstaller:
    """ "Installs" the mock provider, with the banner this server writes at startup."""

    registry_type: str = "pypi"

    def supports(self, registry_type: str) -> bool:
        return registry_type == self.registry_type

    async def install(self, package: Any) -> Any:
        from mcp_hangar.domain.contracts.installer import InstalledPackage
        from mcp_hangar.domain.value_objects import McpServerMode

        return InstalledPackage(
            package_info=package,
            install_path=None,
            command=[sys.executable, str(MOCK_PROVIDER)],
            mode=McpServerMode.SUBPROCESS,
            env={"MOCK_STDERR_BANNER": BANNERS[package.identifier]},
        )

    async def uninstall(self, installed: Any) -> None:
        return None

    def is_runtime_available(self) -> bool:
        return True


class _FakeResolver:
    def resolve(self, packages: list[Any]) -> Any:
        return packages[0] if packages else None

    def get_available_runtimes(self) -> list[str]:
        return ["pypi"]


def _load_handler(context: Any) -> Any:
    """The handler `init_hot_loading` builds, with the registry and installer faked."""
    from mcp_hangar.application.commands.load_handlers import LoadMcpServerHandler
    from mcp_hangar.application.services.secrets_resolver import SecretsResolver
    from mcp_hangar.domain.model import McpServer
    from mcp_hangar.server.bootstrap.logs import LogBuffers
    from mcp_hangar.server.state import get_runtime, get_runtime_mcp_servers

    return LoadMcpServerHandler(
        registry_client=_FakeRegistry(),
        package_resolver=_FakeResolver(),
        secrets_resolver=SecretsResolver(),
        installers=[_FakeInstaller()],
        runtime_store=get_runtime_mcp_servers(),
        event_bus=context.runtime.event_bus,
        mcp_server_factory=lambda **kwargs: McpServer(**kwargs),
        mcp_server_repository=get_runtime().repository,
        log_buffers=LogBuffers(),
    )


class _HotGateway(_LogGateway):
    def load(self, name: str) -> Any:
        payload = self.rpc(None, "tools/call", {"name": "hangar_load", "arguments": {"name": name}})
        return json.loads(payload["result"]["content"][0]["text"])

    def unload(self, mcp_server: str) -> Any:
        payload = self.rpc(None, "tools/call", {"name": "hangar_unload", "arguments": {"mcp_server": mcp_server}})
        return json.loads(payload["result"]["content"][0]["text"])

    def delete(self, mcp_server: str) -> int:
        return self.client.delete(f"/api/mcp_servers/{mcp_server}").status_code


def run(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.infrastructure.persistence.log_buffer import get_log_buffer
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.context import get_context
    from mcp_hangar.server.lifecycle import ServerLifecycle
    from mcp_hangar.server.state import get_runtime_mcp_servers

    path = workdir / "config.yaml"
    _write(path, _config())
    context = bootstrap(config_path=str(path))
    repository = context.runtime.repository
    store = get_runtime_mcp_servers()
    out: dict[str, Any] = {}

    # Only the registry and the installer are replaced; the wiring is the one
    # bootstrap made, including the buffer port.
    get_context().load_mcp_server_handler = _load_handler(context)

    def server_of(mcp_server_id: str) -> Any:
        found = repository.get(mcp_server_id)
        return found if found is not None else store.get_mcp_server(mcp_server_id)

    def wired(mcp_server_id: str) -> bool:
        """Whether the server fills the very buffer the logs API reads for that id."""
        found = server_of(mcp_server_id)
        attached = None if found is None else found._log_buffer
        return attached is not None and get_log_buffer(mcp_server_id) is attached

    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _HotGateway(client, {})

        # The configured server, for the delete case: called so its process
        # starts and writes, exactly as the boot case in #1502's harness.
        out["kept"] = {"logs": gateway.started("kept", BANNERS["kept"]), "wired": wired("kept")}

        loaded = gateway.load("hot-one")
        out["loaded"] = {
            "status": loaded.get("status"),
            "mcp_server_id": loaded.get("mcp_server_id"),
            # `hangar_load` starts the process itself, so the banner is already
            # written by the time the tool answers.
            "logs": gateway.logs_holding("hot-one", BANNERS["hot-one"]),
            "wired": wired("hot-one"),
            "in_repository": repository.exists("hot-one"),
        }
        held = server_of("hot-one")._log_buffer

        # A second load of a server that already holds a buffer: the aggregate
        # keeps the one its reader is filling.
        again = gateway.load("hot-one")
        out["loaded_again"] = {
            "status": again.get("status"),
            "same_buffer": server_of("hot-one")._log_buffer is held,
            "logs": gateway.logs("hot-one"),
        }

        unloaded = gateway.unload("hot-one")
        out["unloaded"] = {
            "status": unloaded.get("status"),
            "logs": gateway.logs("hot-one"),
            "registered": get_log_buffer("hot-one") is not None,
            "in_store": store.exists("hot-one"),
        }

        # A second one, loaded and then deleted rather than unloaded, to show
        # the release is not the unload path's alone.
        gateway.load("hot-two")
        out["deleted"] = {
            "status": gateway.delete("kept"),
            "logs": gateway.logs("kept"),
            "registered": get_log_buffer("kept") is not None,
            "in_repository": repository.exists("kept"),
            # Untouched by the delete beside it.
            "hot_two_registered": get_log_buffer("hot-two") is not None,
            "hot_two_logs": gateway.logs_holding("hot-two", BANNERS["hot-two"]),
        }

    for server in list(repository.get_all().values()):
        server.shutdown()
    for entry in store.list_all():
        entry[0].shutdown()
    return out


def main(workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
