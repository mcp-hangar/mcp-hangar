"""A tool's pin history is served over REST and reaches the SIEM (#1800).

The history is read from the pin-ledger streams of the event store through the
real endpoint, and the CEF line of a pin event carries the digest it pinned, the
one it replaced and who approved it. A tenant-scoped grant sees only its own
tenant's pins.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.event_handlers.audit_handler import AuditEventHandler
from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.application.services.pin_ledger import reconcile_pin_ledger
from mcp_hangar.compliance.cef_formatter import format_audit_record
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import ToolPinRevoked
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server.api.route_permissions import resolve_rule

A, B = "a" * 64, "b" * 64


def _pin(registry, tool="read", sha=A, tenant=None, server="srv"):
    registry.set_config_pin(server, tool, tenant, ToolDigest(tool_name=tool, sha256=sha))


@pytest.fixture
def bus(tmp_path: Path) -> EventBus:
    return EventBus(event_store=SQLiteEventStore(tmp_path / "events.db"))


@pytest.fixture
def client(bus):
    from mcp_hangar.server.api.router import create_api_router

    ctx = Mock()
    ctx.event_bus = bus
    ctx.auth_components = None
    with (
        patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.admin_tools.get_context", return_value=ctx),
    ):
        yield TestClient(create_api_router(auth_components=None), raise_server_exceptions=False)


def test_the_history_is_oldest_first_and_names_the_digest_it_replaced(bus, client):
    registry = ToolProjectionRegistry()
    _pin(registry, sha=A)
    reconcile_pin_ledger(registry, bus)
    _pin(registry, sha=B)
    reconcile_pin_ledger(registry, bus)

    body = client.get("/admin/tools/pins", params={"tool": "read"}).json()

    assert [(p["event"], p["sha256"], p["previous_sha256"]) for p in body["pins"]] == [
        ("recorded", A, None),
        ("recorded", B, A),
    ]
    assert body["pins"][0]["approved_by"] == "config"


def test_a_revocation_is_part_of_the_history(bus, client):
    registry = ToolProjectionRegistry()
    _pin(registry)
    reconcile_pin_ledger(registry, bus)
    bus.publish(ToolPinRevoked(mcp_server_id="srv", tool_name="read", sha256=A))

    pins = client.get("/admin/tools/pins", params={"tool": "read"}).json()["pins"]

    assert [p["event"] for p in pins] == ["recorded", "revoked"]
    assert pins[1]["revoked_by"] == "config"


def test_the_server_parameter_narrows_the_history(bus, client):
    registry = ToolProjectionRegistry()
    _pin(registry, server="one")
    _pin(registry, server="two", sha=B)
    reconcile_pin_ledger(registry, bus)

    both = client.get("/admin/tools/pins", params={"tool": "read"}).json()["pins"]
    one = client.get("/admin/tools/pins", params={"tool": "read", "server": "one"}).json()["pins"]

    assert {p["mcp_server_id"] for p in both} == {"one", "two"}
    assert [p["mcp_server_id"] for p in one] == ["one"]


def test_a_tool_is_required(client):
    assert client.get("/admin/tools/pins").status_code == 400


def test_a_tenant_scoped_grant_sees_only_its_own_tenants_pins(bus, client):
    registry = ToolProjectionRegistry()
    _pin(registry, tenant="tenant:a", sha=A)
    _pin(registry, tenant="tenant:b", sha=B)
    _pin(registry, tenant=None, sha=A)
    reconcile_pin_ledger(registry, bus)

    with patch("mcp_hangar.server.api.admin_tools.confined_tenant", return_value="tenant:a"):
        pins = client.get("/admin/tools/pins", params={"tool": "read"}).json()["pins"]

    assert [(p["tenant_id"], p["sha256"]) for p in pins] == [("tenant:a", A)]


def test_the_route_needs_audit_read_and_is_tenant_aware():
    rule = resolve_rule("GET", "/admin/tools/pins")

    assert rule is not None
    assert rule.permission == ("audit", "read")
    assert rule.tenant_aware


def test_the_cef_line_of_a_pin_carries_the_digests_and_the_approver(bus):
    audit = AuditEventHandler()
    bus.subscribe_to_all(audit.handle, kind=HandlerKind.EFFECT)
    registry = ToolProjectionRegistry()
    _pin(registry, sha=A)
    reconcile_pin_ledger(registry, bus)
    _pin(registry, sha=B)
    reconcile_pin_ledger(registry, bus)

    (record,) = [r for r in audit.store.query() if r.event_type == "ToolPinRecorded" and r.data["sha256"] == B]
    line = format_audit_record(record)

    assert "|410|Tool Pin Recorded|" in line
    assert f"fileHash={B}" in line
    assert f"oldFileHash={A}" in line
    assert "flexString2=config" in line
    assert "cs5=read" in line
