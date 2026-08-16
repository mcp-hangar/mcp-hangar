"""`hangar_load` never succeeded, and nothing said so.

`init_hot_loading` is enabled by default and built its two collaborators empty:
a `RuntimeAvailability` with every field `False`, and `installers=[]`. So
`PackageResolver` filtered out every package, `resolve()` returned `None`, and
the handler answered `status="failed"` with `"Available runtimes: []"` -- on
every call, for every server, since the feature shipped. Behind that stood a
second wall: even with a package selected, an empty installer map meant
`"No installer available for package type: ..."`.

Neither wall was covered. There was no test of `LoadMcpServerHandler` at all,
and none of `init_hot_loading`. These are the two ends: the installers that make
a package runnable, and one whole load ending in `status="loaded"`.
"""

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.commands.commands import LoadMcpServerCommand
from mcp_hangar.application.commands.load_handlers import LoadMcpServerHandler
from mcp_hangar.application.services.package_resolver import PackageResolver
from mcp_hangar.application.services.secrets_resolver import SecretsResolver
from mcp_hangar.domain.contracts.registry import PackageInfo, ServerDetails, TransportInfo
from mcp_hangar.domain.exceptions import InstallationError
from mcp_hangar.domain.value_objects import McpServerMode
from mcp_hangar.infrastructure.installers import npx_installer, runtime_availability, uvx_installer

STDIO = TransportInfo(type="stdio")


def _package(registry_type: str, identifier: str = "mcp-server-time", version: str | None = None) -> PackageInfo:
    return PackageInfo(registry_type=registry_type, identifier=identifier, version=version, transport=STDIO)


def _on_path(monkeypatch, *executables: str) -> None:
    """Pretend exactly these binaries are installed, whatever the host has."""
    monkeypatch.setattr(
        "mcp_hangar.infrastructure.installers.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in executables else None,
    )


class TestTheInstallersResolveACommand:
    async def test_uvx_runs_the_package_by_name(self, monkeypatch):
        _on_path(monkeypatch, "uvx")

        installed = await uvx_installer().install(_package("pypi"))

        assert installed.command == ["uvx", "mcp-server-time"]
        assert installed.mode is McpServerMode.SUBPROCESS
        assert installed.install_path is None, "uvx keeps its own cache; there is no install directory"
        assert installed.cleanup is None, "nothing was installed, so there is nothing to undo"

    async def test_a_version_is_pinned_on_the_specifier(self, monkeypatch):
        _on_path(monkeypatch, "uvx")

        installed = await uvx_installer().install(_package("pypi", version="1.2.3"))

        assert installed.command == ["uvx", "mcp-server-time@1.2.3"]

    async def test_npx_is_told_not_to_prompt(self, monkeypatch):
        """Without `-y`, npx asks before downloading. A subprocess has no TTY to
        answer with, so the question is a hang."""
        _on_path(monkeypatch, "npx")

        installed = await npx_installer().install(_package("npm", identifier="@scope/server"))

        assert installed.command == ["npx", "-y", "@scope/server"]

    async def test_transport_args_are_appended(self, monkeypatch):
        _on_path(monkeypatch, "uvx")
        package = PackageInfo(
            registry_type="pypi",
            identifier="mcp-server-time",
            version=None,
            transport=TransportInfo(type="stdio", args=["--local-timezone", "UTC"]),
        )

        installed = await uvx_installer().install(package)

        assert installed.command == ["uvx", "mcp-server-time", "--local-timezone", "UTC"]

    async def test_a_missing_runtime_names_itself(self, monkeypatch):
        """Rather than letting `ensure_ready()` fail later on a subprocess that
        never starts, which names neither the package nor the binary."""
        _on_path(monkeypatch)

        with pytest.raises(InstallationError, match="uvx is not on PATH"):
            await uvx_installer().install(_package("pypi"))


class TestAvailabilityComesFromTheInstallers:
    def test_a_registry_with_an_installer_and_a_runtime_is_available(self, monkeypatch):
        _on_path(monkeypatch, "uvx", "npx")

        availability = runtime_availability([uvx_installer(), npx_installer()])

        assert (availability.pypi, availability.npm) == (True, True)

    def test_a_registry_whose_runtime_is_absent_is_not(self, monkeypatch):
        _on_path(monkeypatch, "uvx")

        availability = runtime_availability([uvx_installer(), npx_installer()])

        assert (availability.pypi, availability.npm) == (True, False)

    def test_a_registry_with_no_installer_is_not_advertised(self, monkeypatch):
        """`RuntimeAvailability` defaults `binary=True`. Keeping that default
        would have the resolver pick an mcpb package and the very next line
        answer "No installer available for package type: mcpb"."""
        _on_path(monkeypatch, "uvx", "npx", "docker", "podman")

        availability = runtime_availability([uvx_installer(), npx_installer()])

        assert availability.oci is False
        assert availability.binary is False

    def test_the_resolver_built_from_it_can_pick_a_package(self, monkeypatch):
        _on_path(monkeypatch, "uvx")
        resolver = PackageResolver(runtime_availability([uvx_installer(), npx_installer()]))

        assert resolver.get_available_runtimes() == ["pypi"]
        assert resolver.resolve([_package("pypi")]) is not None
        assert resolver.resolve([_package("npm")]) is None, "no npx on this host"


# ---------------------------------------------------------------------------
# One whole load, end to end
# ---------------------------------------------------------------------------


@dataclass
class _FakeRegistry:
    server: ServerDetails

    async def get_server(self, server_id: str) -> ServerDetails | None:
        return self.server if server_id in (self.server.id, self.server.name) else None

    async def search(self, query: str, limit: int = 10) -> list[Any]:
        return []


@dataclass
class _FakeRuntimeStore:
    added: list[Any] = field(default_factory=list)

    def exists(self, mcp_server_id: str) -> bool:
        return False

    def add(self, mcp_server, metadata) -> None:
        self.added.append((mcp_server, metadata))


def _server_details(registry_type: str = "pypi") -> ServerDetails:
    return ServerDetails(
        id="io.github.example/mcp-server-time",
        name="mcp-server-time",
        description="Time tools",
        vendor="example",
        source_url=None,
        is_official=True,
        packages=[_package(registry_type)],
        required_env_vars=[],
    )


def _handler(
    monkeypatch,
    registry_type: str = "pypi",
    approval_gate_available=None,
) -> tuple[LoadMcpServerHandler, list, _FakeRuntimeStore]:
    _on_path(monkeypatch, "uvx", "npx")
    installers = [uvx_installer(), npx_installer()]

    started = []

    def factory(**kwargs):
        mcp_server = MagicMock()
        mcp_server.mcp_server_id = kwargs["mcp_server_id"]
        mcp_server.get_tool_names.return_value = ["get_current_time"]
        mcp_server.ensure_ready.side_effect = lambda: started.append(kwargs)
        return mcp_server

    repository = MagicMock()
    repository.exists.return_value = False
    store = _FakeRuntimeStore()

    handler = LoadMcpServerHandler(
        registry_client=_FakeRegistry(_server_details(registry_type)),
        package_resolver=PackageResolver(runtime_availability(installers)),
        secrets_resolver=SecretsResolver(),
        installers=installers,
        runtime_store=store,
        event_bus=MagicMock(),
        mcp_server_factory=factory,
        mcp_server_repository=repository,
        approval_gate_available=approval_gate_available,
    )
    return handler, started, store


class TestAWholeLoad:
    async def test_it_reaches_loaded(self, monkeypatch):
        """The assertion this feature never had. Before #958 every call to
        `hangar_load` answered `failed` / "No compatible package found
        (missing runtime?)", because bootstrap handed the resolver an
        all-False availability."""
        handler, _, store = _handler(monkeypatch)

        result = await handler.handle(LoadMcpServerCommand(name="mcp-server-time", user_id=None))

        assert result.status == "loaded", result.message
        assert result.tools == [{"name": "get_current_time"}]
        assert len(store.added) == 1

    async def test_the_server_is_started_with_the_installers_command(self, monkeypatch):
        """Ties the two ends together: what the installer resolved is what the
        subprocess is actually launched with."""
        handler, started, _ = _handler(monkeypatch)

        await handler.handle(LoadMcpServerCommand(name="mcp-server-time", user_id=None))

        assert len(started) == 1
        assert started[0]["command"] == ["uvx", "mcp-server-time"]
        assert started[0]["mode"] == "subprocess"

    async def test_a_package_no_installer_handles_still_fails_clearly(self, monkeypatch):
        """The honest half of the fix: `oci` is deliberately not implemented, so
        it must be reported as unavailable rather than picked and then dropped."""
        handler, _, _ = _handler(monkeypatch, registry_type="oci")

        result = await handler.handle(LoadMcpServerCommand(name="mcp-server-time", user_id=None))

        assert result.status == "failed"
        assert "No compatible package found" in result.message
        assert any("['pypi', 'npm']" in w for w in result.warnings), result.warnings


class TestBootstrapWiresItUp:
    """The tests above build the handler by hand. The defect was one layer out:
    `init_hot_loading` constructed a correct handler around empty collaborators,
    so every one of them would have passed while `hangar_load` still failed.
    """

    def _init(self, monkeypatch):
        from mcp_hangar.server.bootstrap import hot_loading

        monkeypatch.setattr(hot_loading, "get_runtime", lambda: MagicMock())
        monkeypatch.setattr(hot_loading, "get_runtime_mcp_servers", lambda: _FakeRuntimeStore())
        return hot_loading.init_hot_loading(MagicMock(), {})

    def test_the_handler_it_returns_has_installers(self, monkeypatch):
        _on_path(monkeypatch, "uvx", "npx")

        load_handler, _ = self._init(monkeypatch)

        assert load_handler is not None
        assert set(load_handler._installers) == {"pypi", "npm"}

    def test_the_resolver_it_returns_can_pick_something(self, monkeypatch):
        """`get_available_runtimes()` answered `[]` for the whole life of this
        feature, and that list is printed in the failure the user sees."""
        _on_path(monkeypatch, "uvx", "npx")

        load_handler, _ = self._init(monkeypatch)

        assert load_handler._package_resolver.get_available_runtimes() == ["pypi", "npm"]

    def test_a_host_with_neither_runtime_says_so_rather_than_pretending(self, monkeypatch):
        _on_path(monkeypatch)

        load_handler, _ = self._init(monkeypatch)

        assert load_handler._package_resolver.get_available_runtimes() == []

    def test_disabled_returns_nothing(self, monkeypatch):
        from mcp_hangar.server.bootstrap import hot_loading

        monkeypatch.setattr(hot_loading, "get_runtime", lambda: MagicMock())
        monkeypatch.setattr(hot_loading, "get_runtime_mcp_servers", lambda: _FakeRuntimeStore())

        assert hot_loading.init_hot_loading(MagicMock(), {"hot_loading": {"enabled": False}}) == (None, None)


class TestARuntimeLoadCanGateATool:
    """The third outcome the YAML surface has, and this one did not (#685).

    `hangar_load` accepted `allow_tools` / `deny_tools` only, so a server
    registered at runtime could be filtered but never put behind approval. The
    guard was the other half of it: `if command.allow_tools or command.deny_tools`
    meant a load asking *only* for approval built no policy at all.
    """

    @pytest.fixture(autouse=True)
    def _leave_the_global_resolver_as_we_found_it(self):
        from mcp_hangar.domain.services import reset_tool_access_resolver

        reset_tool_access_resolver()
        yield
        reset_tool_access_resolver()

    def _policy_for(self, mcp_server_id: str):
        from mcp_hangar.domain.services import get_tool_access_resolver

        registered = dict(get_tool_access_resolver().iter_registered_policies())
        return registered.get(f"mcp_server:{mcp_server_id}")

    async def test_approval_tools_reaches_the_registered_policy(self, monkeypatch):
        handler, _, _ = _handler(monkeypatch, approval_gate_available=lambda: True)

        result = await handler.handle(
            LoadMcpServerCommand(name="mcp-server-time", user_id=None, approval_tools=["get_*"])
        )

        assert result.status == "loaded", result.message
        policy = self._policy_for(result.mcp_server_id)
        assert policy is not None, "a load that gates a tool must register a policy"
        assert policy.approval_list == ("get_*",)
        assert policy.requires_approval("get_current_time")

    async def test_approval_alone_is_enough_to_build_a_policy(self, monkeypatch):
        """The old guard only looked at allow/deny, so this case built nothing."""
        handler, _, _ = _handler(monkeypatch, approval_gate_available=lambda: True)

        result = await handler.handle(LoadMcpServerCommand(name="mcp-server-time", user_id=None, approval_tools=["*"]))

        assert self._policy_for(result.mcp_server_id) is not None

    async def test_the_other_two_lists_still_arrive(self, monkeypatch):
        handler, _, _ = _handler(monkeypatch, approval_gate_available=lambda: True)

        result = await handler.handle(
            LoadMcpServerCommand(
                name="mcp-server-time",
                user_id=None,
                allow_tools=["get_*"],
                deny_tools=["delete_*"],
            )
        )

        policy = self._policy_for(result.mcp_server_id)
        assert (policy.allow_list, policy.deny_list) == (("get_*",), ("delete_*",))

    async def test_a_load_with_no_lists_registers_no_policy(self, monkeypatch):
        handler, _, _ = _handler(monkeypatch, approval_gate_available=lambda: True)

        result = await handler.handle(LoadMcpServerCommand(name="mcp-server-time", user_id=None))

        assert self._policy_for(result.mcp_server_id) is None


class TestGatingWithoutAGateIsRefused:
    """A policy nothing can enforce is worse than a refused load: the tools are
    listed, the calls run, and the deployment believes a human is deciding.
    This is the startup check in `bootstrap/reachability.py` asked at the only
    moment a runtime policy exists.
    """

    @pytest.fixture(autouse=True)
    def _leave_the_global_resolver_as_we_found_it(self):
        from mcp_hangar.domain.services import reset_tool_access_resolver

        reset_tool_access_resolver()
        yield
        reset_tool_access_resolver()

    async def test_no_gate_refuses_the_load(self, monkeypatch):
        handler, started, _ = _handler(monkeypatch, approval_gate_available=lambda: False)

        result = await handler.handle(
            LoadMcpServerCommand(name="mcp-server-time", user_id=None, approval_tools=["get_*"])
        )

        assert result.status == "failed"
        assert "approval" in result.message
        assert started == [], "nothing should be installed or started for a refused load"

    async def test_an_unwired_handler_is_treated_as_no_gate(self, monkeypatch):
        """The default. An embedder that never passed the probe gets the
        fail-closed answer rather than an unenforced policy."""
        handler, _, _ = _handler(monkeypatch)

        result = await handler.handle(
            LoadMcpServerCommand(name="mcp-server-time", user_id=None, approval_tools=["get_*"])
        )

        assert result.status == "failed"

    async def test_a_probe_that_raises_is_a_no(self, monkeypatch):
        handler, _, _ = _handler(monkeypatch, approval_gate_available=_raises)

        result = await handler.handle(
            LoadMcpServerCommand(name="mcp-server-time", user_id=None, approval_tools=["get_*"])
        )

        assert result.status == "failed"

    async def test_a_load_that_gates_nothing_is_unaffected(self, monkeypatch):
        handler, _, _ = _handler(monkeypatch, approval_gate_available=lambda: False)

        result = await handler.handle(
            LoadMcpServerCommand(name="mcp-server-time", user_id=None, deny_tools=["delete_*"])
        )

        assert result.status == "loaded", result.message


def _raises() -> bool:
    raise RuntimeError("context not built")
