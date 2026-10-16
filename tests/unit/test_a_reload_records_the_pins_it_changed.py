"""A reload appends the pins it added, changed or removed to the ledger (#1800).

The reload handler is the one bootstrap wires, over the real loader and a real
event store, and the registry is the one the loaded file fills.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.application.services.pin_ledger import ledger_state
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import ServerConfigLoader, load_configuration
from mcp_hangar.server.state import GROUPS, get_runtime
from mcp_hangar.stream_ids import PIN_LEDGER, stream_id_for

SERVER = "pinned"
A, B = "a" * 64, "b" * 64


def _reset() -> None:
    reset_tool_access_resolver()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    if repository.exists(SERVER):
        repository.remove(SERVER)
    GROUPS.clear()
    get_tool_projection_registry().clear_config_pins()


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def _file(pins: dict[str, str]) -> dict[str, Any]:
    server: dict[str, Any] = {"mode": "subprocess", "command": ["python", "-c", "pass"]}
    if pins:
        server["tool_projection"] = {"pins": pins}
    return {"mcp_servers": {SERVER: server}}


def _state(bus: EventBus):
    return ledger_state(bus.event_store.read_stream(stream_id_for(PIN_LEDGER, SERVER)))


def test_a_reload_records_the_changes_and_only_the_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    bus = EventBus(event_store=SQLiteEventStore(tmp_path / "events.db"))
    handler = ReloadConfigurationHandler(
        get_runtime().repository, bus, str(config_path), config_loader=ServerConfigLoader(), groups=GROUPS
    )

    def apply(pins: dict[str, str], *, boot: bool = False) -> None:
        config_path.write_text(yaml.safe_dump(_file(pins), sort_keys=False))
        if boot:
            load_configuration(str(config_path))
        else:
            handler.handle(ReloadConfigurationCommand(requested_by="test"))

    apply({"read": A}, boot=True)
    apply({"read": A})
    assert _state(bus) == {("read", None): (A, None)}

    apply({"read": B, "write": A})
    assert _state(bus) == {("read", None): (B, None), ("write", None): (A, None)}

    apply({"write": A})
    assert _state(bus) == {("write", None): (A, None)}
    assert len(bus.event_store.read_stream(stream_id_for(PIN_LEDGER, SERVER))) == 4, (
        "read A, read B, write A, read revoked: the unchanged reloads added nothing"
    )


def test_a_ledger_that_cannot_be_written_does_not_fail_the_reload(tmp_path: Path) -> None:
    class BrokenBus:
        @property
        def event_store(self) -> Any:
            raise OSError("the store is gone")

        def publish(self, event: Any) -> None:
            pass

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(_file({"read": A}), sort_keys=False))
    load_configuration(str(config_path))
    handler = ReloadConfigurationHandler(
        get_runtime().repository, BrokenBus(), str(config_path), config_loader=ServerConfigLoader(), groups=GROUPS
    )

    handler.handle(ReloadConfigurationCommand(requested_by="test"))  # must not raise
