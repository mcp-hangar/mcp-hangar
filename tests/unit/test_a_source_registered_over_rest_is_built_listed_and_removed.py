"""A discovery source registered over REST is built, listed and removed (#1674).

The handler stored a spec in the `DiscoveryRegistry` and built nothing, so
`GET /discovery/sources` -- which reads the orchestrator -- never showed it and
a scan never used it. The pieces here are the production ones: the orchestrator
running on its own loop, the registry, the handlers on a command bus, and the
factory the bootstrap injects (`create_source`).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_hangar.application.commands.discovery_commands import (
    DeregisterDiscoverySourceCommand,
    RegisterDiscoverySourceCommand,
    ToggleDiscoverySourceCommand,
)
from mcp_hangar.application.commands.discovery_handlers import register_discovery_handlers
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.application.discovery.discovery_registry import DiscoveryRegistry
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.discovery.registry import create_source


class World:
    def __init__(self, orchestrator: DiscoveryOrchestrator, registry: DiscoveryRegistry, bus: CommandBus) -> None:
        self.orchestrator = orchestrator
        self.registry = registry
        self.bus = bus

    def sources(self) -> list[dict]:
        loop = self.orchestrator._loop
        assert loop is not None
        return asyncio.run_coroutine_threadsafe(self.orchestrator.get_sources_status(), loop).result(timeout=10)


@pytest.fixture
def world() -> Iterator[World]:
    orchestrator = DiscoveryOrchestrator(
        config=DiscoveryConfig.from_dict({"enabled": True, "refresh_interval_s": 3600})
    )
    registry = DiscoveryRegistry(orchestrator=orchestrator)
    bus = CommandBus()
    register_discovery_handlers(bus, registry, source_factory=create_source)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(orchestrator.start(), loop).result(timeout=10)
    try:
        yield World(orchestrator, registry, bus)
    finally:
        asyncio.run_coroutine_threadsafe(orchestrator.stop(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
        loop.close()


def _register(world: World, tmp_path: Path, **overrides) -> dict:
    command = RegisterDiscoverySourceCommand(
        source_type=overrides.pop("source_type", "filesystem"),
        mode="additive",
        enabled=overrides.pop("enabled", True),
        config=overrides.pop("config", {"path": str(tmp_path), "watch": False}),
    )
    return world.bus.send(command)


def test_a_registered_source_is_listed(world, tmp_path):
    assert world.sources() == []

    result = _register(world, tmp_path)

    assert result["registered"] is True
    (listed,) = world.sources()
    assert listed["source_type"] == "filesystem"
    assert listed["id"] == result["source_id"], "the id POST returns is the one the listing shows"
    assert world.registry.get_source(result["source_id"]) is not None


def test_a_source_registered_disabled_is_listed_disabled(world, tmp_path):
    _register(world, tmp_path, enabled=False)

    (source,) = world.sources()
    assert source["is_enabled"] is False


def test_deregistering_removes_the_live_source_too(world, tmp_path):
    source_id = _register(world, tmp_path)["source_id"]

    world.bus.send(DeregisterDiscoverySourceCommand(source_id=source_id))

    assert world.sources() == []
    assert world.registry.get_source(source_id) is None


def test_a_second_source_of_a_held_type_is_refused_and_the_first_stays(world, tmp_path):
    first = _register(world, tmp_path)["source_id"]

    with pytest.raises(ValidationError, match="already held"):
        _register(world, tmp_path / "other")

    assert [s["source_type"] for s in world.sources()] == ["filesystem"]
    assert world.registry.get_source(first) is not None


def test_an_unknown_source_type_is_refused_not_stored(world, tmp_path):
    with pytest.raises(ValidationError):
        _register(world, tmp_path, source_type="nosuch")

    assert world.registry.get_all_sources() == []
    assert world.sources() == []


def test_a_source_the_config_built_is_not_removed_by_deregistering_a_spec_of_its_type(world, tmp_path):
    """Only a source this registry built is removed with its spec."""
    world.orchestrator.add_source(create_source("filesystem", {"path": str(tmp_path), "watch": False}))
    from mcp_hangar.domain.value_objects.discovery import DiscoverySourceSpec, config_source_id

    spec_id = config_source_id("filesystem")
    world.registry.register_source(
        DiscoverySourceSpec(
            source_id=spec_id,
            source_type="filesystem",
            mode=world.orchestrator.get_source("filesystem").mode,
        )
    )

    world.bus.send(DeregisterDiscoverySourceCommand(source_id=spec_id))

    assert [s["source_type"] for s in world.sources()] == ["filesystem"]


def test_toggling_a_registered_source_reaches_the_live_one(world, tmp_path):
    source_id = _register(world, tmp_path)["source_id"]

    world.bus.send(ToggleDiscoverySourceCommand(source_id=source_id, enabled=False))

    (source,) = world.sources()
    assert source["is_enabled"] is False
