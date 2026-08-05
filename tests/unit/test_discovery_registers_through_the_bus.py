"""A discovered server joins the fleet the same way a requested one does.

There were two doors into the fleet and only one was guarded. The CRUD path
sends `CreateMcpServerCommand`, whose handler refuses a duplicate, runs an SSRF
check on a remote endpoint, and publishes `McpServerRegistered`. Discovery, with
`auto_register` on by default, built the aggregate itself and called
`repository.add` -- so a server could appear automatically, unvalidated, leaving
one log line and no record of it happening at all.

That is three separate holes with one cause, which is why the fix is one line of
routing rather than three patches.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler
from mcp_hangar.domain.events import McpServerRegistered
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        self.published.extend(events)
        return len(events) - 1


class _CapturingCommandBus:
    """Stands in for the real bus, and actually executes the real handler.

    A mock that only records the command would pass even if the handler could
    not accept it -- which is the failure this change is most exposed to, since
    discovery assembles the command's kwargs from a different vocabulary.
    """

    def __init__(self, repository, event_bus) -> None:
        self.sent: list[Any] = []
        self._handler = CreateMcpServerHandler(repository=repository, event_bus=event_bus)

    def send(self, command: Any) -> Any:
        self.sent.append(command)
        return self._handler.handle(command)


def _discovered(name: str = "found", *, mode: str = "container", **conn: Any) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        mode=mode,
        source_type="kubernetes",
        connection_info={"image": "ghcr.io/x/y:1", **conn},
    )


@pytest.fixture
def wired(monkeypatch):
    repo = InMemoryMcpServerRepository()
    events = _RecordingBus()
    commands = _CapturingCommandBus(repo, events)
    monkeypatch.setattr(
        bootstrap_discovery,
        "get_runtime",
        lambda: SimpleNamespace(repository=repo, command_bus=commands, event_bus=events),
    )
    return repo, events, commands


class TestRegistrationGoesThroughTheCommand:
    @pytest.mark.asyncio
    async def test_a_discovered_server_is_registered(self, wired) -> None:
        repo, _events, commands = wired

        assert await bootstrap_discovery._on_mcp_server_register(_discovered()) is True

        assert isinstance(commands.sent[0], CreateMcpServerCommand)
        assert repo.exists("found")

    @pytest.mark.asyncio
    async def test_the_registration_is_recorded_with_its_provenance(self, wired) -> None:
        _repo, events, _commands = wired

        await bootstrap_discovery._on_mcp_server_register(_discovered())

        registered = [e for e in events.published if isinstance(e, McpServerRegistered)]
        assert len(registered) == 1
        # Which door it came through, and which source -- the CRUD path has
        # always carried this, and discovery carried nothing.
        assert registered[0].source == "discovery:kubernetes"

    @pytest.mark.asyncio
    async def test_docker_volumes_survive_the_trip(self, wired) -> None:
        # The command had no `volumes` field. Routing through it without adding
        # one would have dropped mounts for every discovered container --
        # silently, because the aggregate accepts the argument and simply never
        # received it.
        _repo, _events, commands = wired

        await bootstrap_discovery._on_mcp_server_register(_discovered(volumes=["/data:/data"]))

        assert commands.sent[0].volumes == ["/data:/data"]


class TestGuardsDiscoveryUsedToBypass:
    @pytest.mark.asyncio
    async def test_a_duplicate_is_refused(self, wired) -> None:
        _repo, _events, _commands = wired
        await bootstrap_discovery._on_mcp_server_register(_discovered("twice"))

        # The handler raises; the callback's barrier turns that into "not
        # registered" rather than letting discovery crash the cycle.
        assert await bootstrap_discovery._on_mcp_server_register(_discovered("twice")) is False

    @pytest.mark.asyncio
    async def test_a_remote_endpoint_is_ssrf_checked(self, wired) -> None:
        # The handler runs validate_no_ssrf for remote mode. The old path built
        # the aggregate directly and never reached it, so a discovery source
        # could point the gateway at link-local metadata.
        _repo, _events, commands = wired

        result = await bootstrap_discovery._on_mcp_server_register(
            _discovered("meta", mode="http", host="169.254.169.254", port=80)
        )

        assert result is False, "an SSRF-shaped endpoint must not become a registered server"
        assert not commands.sent or not any(c.mcp_server_id == "meta" for c in commands.sent[1:])


class TestTheCommandStillServesItsOriginalCaller:
    def test_volumes_default_to_none(self) -> None:
        # The new field must not become required for the CRUD path.
        command = CreateMcpServerCommand(mcp_server_id="x", mode="subprocess", command=["echo"])
        assert command.volumes is None

    def test_the_handler_accepts_a_command_without_volumes(self) -> None:
        repo = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(repository=repo, event_bus=_RecordingBus())

        handler.handle(CreateMcpServerCommand(mcp_server_id="x", mode="subprocess", command=["echo"]))

        assert repo.exists("x")

    def test_a_duplicate_still_raises_for_the_crud_caller(self) -> None:
        repo = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(repository=repo, event_bus=_RecordingBus())
        command = CreateMcpServerCommand(mcp_server_id="x", mode="subprocess", command=["echo"])
        handler.handle(command)

        with pytest.raises(ValidationError):
            handler.handle(command)


class TestTheNewFieldsDoNotRelaxAnything:
    """Adding a field must not change what omitting it means.

    `read_only` defaults to True on the aggregate: a container is hardened
    unless someone says otherwise. A first version of this change gave the
    command `bool | None = None` and coerced it with `bool()`, which turned
    "the caller did not say" into "turn hardening off" for every CRUD-created
    server. mypy caught the type, not the meaning.
    """

    def test_a_server_created_without_read_only_is_still_hardened(self) -> None:
        repo = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(repository=repo, event_bus=_RecordingBus())

        handler.handle(CreateMcpServerCommand(mcp_server_id="x", mode="docker", image="i"))

        assert repo.get("x")._read_only is True

    def test_the_command_default_matches_the_aggregate(self) -> None:
        import inspect

        from mcp_hangar.domain.model.mcp_server import McpServer

        aggregate_default = inspect.signature(McpServer).parameters["read_only"].default
        command_default = CreateMcpServerCommand(mcp_server_id="x", mode="docker", image="i").read_only
        assert command_default == aggregate_default, (
            "the command's default must track the aggregate's, or omitting the field "
            "means something different depending on which door you came through"
        )

    def test_it_can_still_be_turned_off_deliberately(self) -> None:
        repo = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(repository=repo, event_bus=_RecordingBus())

        handler.handle(CreateMcpServerCommand(mcp_server_id="x", mode="docker", image="i", read_only=False))

        assert repo.get("x")._read_only is False
