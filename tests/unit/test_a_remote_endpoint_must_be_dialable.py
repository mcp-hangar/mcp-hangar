"""A remote endpoint the HTTP launcher refuses is refused when it is set (#2056).

The launcher's rule -- http or https, with a host -- ran only at start, so a
`file://` endpoint was accepted over REST, stored and listed, and failed at every
start.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, UpdateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, UpdateMcpServerHandler
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter

GOOD = "https://203.0.113.5:8443/mcp"
BAD = ["file:///etc/passwd", "gopher://203.0.113.5/", "ws://203.0.113.5/mcp", "https:///nohost", "203.0.113.5/mcp"]


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        return 0


@pytest.fixture
def handlers(tmp_path):
    configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    writer = RepositoryFleetWriter(configs)
    fleet = InMemoryMcpServerRepository()
    common = {"repository": fleet, "event_bus": _Bus(), "fleet_writer": writer}
    yield CreateMcpServerHandler(**common), UpdateMcpServerHandler(**common), fleet
    writer.close()


@pytest.mark.parametrize("endpoint", BAD)
def test_create_refuses_it_and_stores_nothing(handlers, endpoint) -> None:
    create, _, fleet = handlers

    with pytest.raises(ValidationError, match="http or https"):
        create.handle(CreateMcpServerCommand(mcp_server_id="far", mode="remote", endpoint=endpoint))

    assert not fleet.exists("far")


@pytest.mark.parametrize("endpoint", BAD)
def test_update_refuses_it_and_keeps_the_old_one(handlers, endpoint) -> None:
    create, update, fleet = handlers
    create.handle(CreateMcpServerCommand(mcp_server_id="far", mode="remote", endpoint=GOOD))

    with pytest.raises(ValidationError, match="http or https"):
        update.handle(UpdateMcpServerCommand(mcp_server_id="far", endpoint=endpoint))

    assert fleet.get("far")._endpoint == GOOD


def test_an_http_endpoint_is_accepted_as_before(handlers) -> None:
    create, update, fleet = handlers
    create.handle(CreateMcpServerCommand(mcp_server_id="far", mode="remote", endpoint=GOOD))

    update.handle(UpdateMcpServerCommand(mcp_server_id="far", endpoint="http://203.0.113.9:8080/mcp"))

    assert fleet.get("far")._endpoint == "http://203.0.113.9:8080/mcp"
