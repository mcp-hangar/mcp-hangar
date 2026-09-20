"""A hot-loaded server gains a log buffer, and unload and delete release it (#1506).

`LoadMcpServerHandler` built a server at runtime and attached no buffer, so no
stderr reader was ever started for it and the per-server log the API serves
stayed empty. `UnloadMcpServerHandler` and `DeleteMcpServerHandler` released
none, so the registry entry outlived the server under an id that was free again.

The handlers reach the buffer registry through `ILogBuffers`, because
`.importlinter` puts `infrastructure` above `application`. These drive the three
handlers against a recording port, and the real adapter against the real
registry. The end-to-end version, with a real subprocess and the logs endpoint,
is `tests/integration/test_a_hot_loaded_server_has_its_log.py`.
"""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.commands.commands import LoadMcpServerCommand, UnloadMcpServerCommand
from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import DeleteMcpServerHandler
from mcp_hangar.application.commands.load_handlers import LoadMcpServerHandler, UnloadMcpServerHandler
from mcp_hangar.domain.contracts.installer import InstalledPackage
from mcp_hangar.domain.contracts.registry import PackageInfo, ServerDetails, TransportInfo
from mcp_hangar.domain.value_objects import McpServerMode, McpServerState
from mcp_hangar.infrastructure.persistence.log_buffer import (
    McpServerLogBuffer,
    clear_log_buffer_registry,
    get_log_buffer,
    set_log_buffer,
)
from mcp_hangar.infrastructure.runtime_store import LoadMetadata
from mcp_hangar.server.bootstrap.logs import LogBuffers

PACKAGE = PackageInfo(registry_type="pypi", identifier="hot", version=None, transport=TransportInfo(type="stdio"))


# ---------------------------------------------------------------------------
# Doubles: the registry and the installer are the network, and stand in here.
# ---------------------------------------------------------------------------


@dataclass
class _Recorder:
    """An `ILogBuffers` that records the calls instead of touching the registry."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    held: set[str] = field(default_factory=set)

    def attach(self, mcp_server_id: str, mcp_server: Any) -> bool:
        self.calls.append(("attach", mcp_server_id))
        if mcp_server_id in self.held:
            return False
        self.held.add(mcp_server_id)
        return True

    def release(self, mcp_server_id: str) -> None:
        self.calls.append(("release", mcp_server_id))
        self.held.discard(mcp_server_id)


@dataclass
class _Events:
    """A recording logger, because these modules render straight to stderr."""

    entries: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def info(self, event: str, **fields: Any) -> None:
        self.entries.append((event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.entries.append((event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self.entries.append((event, fields))

    def names(self) -> list[str]:
        return [event for event, _ in self.entries]

    def fields_of(self, event: str) -> dict[str, Any]:
        return next(fields for name, fields in self.entries if name == event)


@dataclass
class _FakeRegistry:
    server: ServerDetails

    async def get_server(self, server_id: str) -> ServerDetails | None:
        return self.server if server_id in (self.server.id, self.server.name) else None

    async def search(self, query: str, limit: int = 10) -> list[Any]:
        return []


@dataclass
class _FakeInstaller:
    registry_type: str = "pypi"

    def supports(self, registry_type: str) -> bool:
        return registry_type == self.registry_type

    async def install(self, package: PackageInfo) -> InstalledPackage:
        return InstalledPackage(
            package_info=package,
            install_path=None,
            command=["hot-server"],
            mode=McpServerMode.SUBPROCESS,
        )

    async def uninstall(self, installed: InstalledPackage) -> None:
        return None

    def is_runtime_available(self) -> bool:
        return True


@dataclass
class _FakeResolver:
    def resolve(self, packages: list[PackageInfo]) -> PackageInfo | None:
        return packages[0] if packages else None

    def get_available_runtimes(self) -> list[str]:
        return ["pypi"]


@dataclass
class _FakeRuntimeStore:
    entries: dict[str, Any] = field(default_factory=dict)

    def exists(self, mcp_server_id: str) -> bool:
        return mcp_server_id in self.entries

    def add(self, mcp_server: Any, metadata: LoadMetadata) -> None:
        self.entries[str(mcp_server.mcp_server_id)] = (mcp_server, metadata)

    def get(self, mcp_server_id: str) -> Any:
        return self.entries.get(mcp_server_id)

    def remove(self, mcp_server_id: str) -> Any:
        entry = self.entries.pop(mcp_server_id, None)
        return None if entry is None else entry[0]


def _details() -> ServerDetails:
    return ServerDetails(
        id="hot-one",
        name="hot-one",
        description="a hot-loaded server",
        vendor=None,
        source_url=None,
        is_official=True,
        packages=[PACKAGE],
        required_env_vars=[],
    )


def _load_handler(log_buffers: Any, order: list[str] | None = None) -> tuple[LoadMcpServerHandler, _FakeRuntimeStore]:
    def factory(**kwargs: Any) -> Any:
        mcp_server = MagicMock()
        mcp_server.mcp_server_id = kwargs["mcp_server_id"]
        mcp_server._log_buffer = None
        mcp_server.get_tool_names.return_value = ["add"]
        if order is not None:
            mcp_server.ensure_ready.side_effect = lambda: order.append("ensure_ready")
        return mcp_server

    repository = MagicMock()
    repository.exists.return_value = False
    store = _FakeRuntimeStore()
    handler = LoadMcpServerHandler(
        registry_client=_FakeRegistry(_details()),
        package_resolver=_FakeResolver(),
        secrets_resolver=MagicMock(
            resolve=MagicMock(return_value=MagicMock(all_resolved=True, resolved={}, missing=[])),
        ),
        installers=[_FakeInstaller()],
        runtime_store=store,
        event_bus=MagicMock(),
        mcp_server_factory=factory,
        mcp_server_repository=repository,
        log_buffers=log_buffers,
    )
    return handler, store


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_log_buffer_registry()
    yield
    clear_log_buffer_registry()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


class TestALoadedServerGetsABuffer:
    async def test_the_load_attaches_one(self):
        recorder = _Recorder()
        handler, _ = _load_handler(recorder)

        result = await handler.handle(LoadMcpServerCommand(name="hot-one", user_id=None))

        assert result.status == "loaded", result.message
        assert recorder.calls == [("attach", "hot-one")]

    async def test_it_is_attached_before_the_process_starts(self):
        """The ordering the feature rests on, not an incidental one.

        `_create_client` spawns the stderr reader only when a buffer is already
        set, so a buffer attached after `ensure_ready()` is registered for the
        endpoint to read and never written to.
        """
        order: list[str] = []

        class _Ordered(_Recorder):
            def attach(self, mcp_server_id: str, mcp_server: Any) -> bool:
                order.append("attach")
                return super().attach(mcp_server_id, mcp_server)

        handler, _ = _load_handler(_Ordered(), order)

        await handler.handle(LoadMcpServerCommand(name="hot-one", user_id=None))

        assert order == ["attach", "ensure_ready"]

    async def test_it_says_so(self, monkeypatch):
        events = _Events()
        monkeypatch.setattr("mcp_hangar.application.commands.load_handlers.logger", events)
        handler, _ = _load_handler(_Recorder())

        await handler.handle(LoadMcpServerCommand(name="hot-one", user_id=None))

        assert events.fields_of("log_buffer_attached_to_mcp_server") == {"mcp_server_id": "hot-one"}

    async def test_a_load_without_the_port_still_loads(self):
        """The port is optional, so a caller that wires none is not broken by it."""
        handler, store = _load_handler(None)

        result = await handler.handle(LoadMcpServerCommand(name="hot-one", user_id=None))

        assert result.status == "loaded"
        assert "hot-one" in store.entries


# ---------------------------------------------------------------------------
# Unloading and deleting
# ---------------------------------------------------------------------------


class TestAnUnloadedServerReleasesIts:
    def test_the_unload_releases_it(self, monkeypatch):
        events = _Events()
        monkeypatch.setattr("mcp_hangar.application.commands.load_handlers.logger", events)
        recorder = _Recorder(held={"hot-one"})
        store = _FakeRuntimeStore()
        mcp_server = MagicMock()
        mcp_server.mcp_server_id = "hot-one"
        store.entries["hot-one"] = (
            mcp_server,
            LoadMetadata(loaded_at=__import__("datetime").datetime.now(), loaded_by=None, source="x", verified=True),
        )
        handler = UnloadMcpServerHandler(store, MagicMock(), log_buffers=recorder)

        handler.handle(UnloadMcpServerCommand(mcp_server_id="hot-one", user_id=None))

        assert recorder.calls == [("release", "hot-one")]
        assert events.fields_of("log_buffer_released") == {"mcp_server_id": "hot-one"}

    def test_the_delete_releases_it(self, monkeypatch):
        events = _Events()
        monkeypatch.setattr("mcp_hangar.application.commands.crud_handlers.logger", events)
        recorder = _Recorder(held={"configured"})
        mcp_server = MagicMock()
        mcp_server.mcp_server_id = "configured"
        mcp_server.state = McpServerState.COLD
        repository = MagicMock()
        repository.get.return_value = mcp_server
        handler = DeleteMcpServerHandler(repository=repository, event_bus=MagicMock(), log_buffers=recorder)

        handler.handle(DeleteMcpServerCommand(mcp_server_id="configured"))

        assert recorder.calls == [("release", "configured")]
        assert events.fields_of("log_buffer_released") == {"mcp_server_id": "configured"}


# ---------------------------------------------------------------------------
# The adapter, against the real registry
# ---------------------------------------------------------------------------


class TestTheAdapter:
    def test_it_registers_the_buffer_the_logs_api_reads(self):
        mcp_server = MagicMock()
        mcp_server._log_buffer = None
        mcp_server.set_log_buffer.side_effect = lambda buffer: setattr(mcp_server, "_log_buffer", buffer)

        assert LogBuffers().attach("hot-one", mcp_server) is True
        assert get_log_buffer("hot-one") is mcp_server._log_buffer

    def test_a_server_that_holds_one_keeps_it(self):
        """#1502's rule. A running server's reader fills the buffer it was
        started with, so replacing the registered one leaves it writing where
        nothing reads."""
        held = McpServerLogBuffer(mcp_server_id="hot-one")
        set_log_buffer("hot-one", held)
        mcp_server = MagicMock()
        mcp_server._log_buffer = held

        assert LogBuffers().attach("hot-one", mcp_server) is False
        assert get_log_buffer("hot-one") is held
        mcp_server.set_log_buffer.assert_not_called()

    def test_releasing_drops_the_entry(self):
        set_log_buffer("hot-one", McpServerLogBuffer(mcp_server_id="hot-one"))

        LogBuffers().release("hot-one")

        assert get_log_buffer("hot-one") is None

    def test_releasing_an_unknown_id_is_quiet(self):
        LogBuffers().release("never-existed")

        assert get_log_buffer("never-existed") is None
