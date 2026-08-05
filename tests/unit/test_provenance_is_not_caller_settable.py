"""Provenance is established by the path a registration took, never by its body.

The SSRF policy is asymmetric on purpose: a discovery source may register a
private address, a human may not. That asymmetry is only worth anything if the
side it favours cannot be claimed. `source` is free text an operator reads and
some routes forward, so a policy keyed on `source.startswith("discovery:")`
would hand the keys to whoever it was meant to constrain.

So the REST route names every field it builds the command from, and provenance
is not among them.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.domain.value_objects.provenance import Provenance


@pytest.fixture(autouse=True)
def _leave_the_global_context_as_we_found_it():
    from mcp_hangar.server.context import reset_context

    reset_context()
    yield
    reset_context()


@pytest.fixture
def sent(monkeypatch):
    """Mount just the mcp_servers routes and capture what reaches the bus."""
    context = Mock()
    context.command_bus = Mock()
    context.query_bus = Mock()
    # A plain dict, because the route serializes whatever the bus returns and a
    # bare Mock has an infinite attribute tree to walk.
    context.command_bus.send.return_value = {"mcp_server_id": "spoofed", "created": True}

    from mcp_hangar.server.api.mcp_servers import mcp_server_routes

    monkeypatch.setattr("mcp_hangar.server.api.mcp_servers.get_context", lambda: context)
    # The route hands the command to `dispatch_command`, which resolves the bus
    # through the middleware module's own `get_context` -- patching only the
    # route module leaves the real bus in place and the request fails on a
    # missing handler rather than on what this file is about.
    monkeypatch.setattr("mcp_hangar.server.api.middleware.get_context", lambda: context)
    monkeypatch.setattr("mcp_hangar.server.api.mcp_servers._check_permission", lambda *a, **k: None)

    client = TestClient(Starlette(routes=[Mount("/mcp_servers", routes=mcp_server_routes)]))
    return client, context.command_bus


class TestARequestCannotClaimDiscoveryProvenance:
    def test_a_body_naming_provenance_does_not_set_it(self, sent) -> None:
        client, bus = sent

        client.post(
            "/mcp_servers/",
            json={
                "mcp_server_id": "spoofed",
                "mode": "subprocess",
                "command": ["echo"],
                "provenance": "discovery",
            },
        )

        command = bus.send.call_args[0][0]
        assert command.provenance is Provenance.HUMAN

    def test_a_body_naming_source_does_not_set_it(self, sent) -> None:
        # The string this policy deliberately does not read. Kept honest here so
        # nobody reintroduces `source` as a security input by widening the route.
        client, bus = sent

        client.post(
            "/mcp_servers/",
            json={
                "mcp_server_id": "spoofed",
                "mode": "subprocess",
                "command": ["echo"],
                "source": "discovery:docker",
            },
        )

        assert bus.send.call_args[0][0].source == "api"

    def test_a_body_cannot_supply_runtime_addresses(self, sent) -> None:
        # Passing these would be the other half of the same spoof: claim the
        # provenance, then claim the address it is scoped to.
        client, bus = sent

        client.post(
            "/mcp_servers/",
            json={
                "mcp_server_id": "spoofed",
                "mode": "remote",
                "endpoint": "http://10.0.0.5:8080",
                "runtime_addresses": ["10.0.0.5"],
            },
        )

        command = bus.send.call_args[0][0]
        assert command.runtime_addresses is None


class TestTheDefaultsAreTheStrictOnes:
    def test_a_command_built_without_provenance_is_human(self) -> None:
        from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand

        command = CreateMcpServerCommand(mcp_server_id="x", mode="subprocess", command=["echo"])

        assert command.provenance is Provenance.HUMAN
        assert command.runtime_addresses is None
