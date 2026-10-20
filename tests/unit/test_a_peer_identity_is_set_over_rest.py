"""A server's endpoint and peer identity are set over REST (#1948, ADR-030 §4b, §4e, §4f, §9).

The operator registers a key-pinned server before its pod exists, sets the
endpoint once the pod has an address, and clears it when the pod goes. Each of
those, and every change to a key list, needs `mcp_servers:peer_identity`. One
update may rotate keys, but may not replace the whole list under one `key_ref`:
a single write must not cut every key a peer is trusted with at once.
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateMcpServerCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    AddGroupMemberHandler,
    CreateMcpServerHandler,
    UpdateMcpServerHandler,
    register_crud_handlers,
)
from mcp_hangar.domain.events import McpServerUpdated
from mcp_hangar.domain.exceptions import (
    McpServerDeclaredInConfigError,
    PeerIdentityChangeDenied,
    PeerKeyNameTaken,
    ValidationError,
)
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.security.ssrf import SsrfBlocked
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import (
    InMemoryMcpServerConfigRepository,
    SQLiteMcpServerConfigRepository,
)
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.server.bootstrap.peer_identity_authority import ContextPeerIdentityAuthority

ENDPOINT = "https://203.0.113.5:8443"
MOVED = "https://203.0.113.9:8443"
OLD, NEW, NEXT = ("sha256:" + c * 64 for c in "abc")


def _tls(*pins: str, key_ref: str = "lineage") -> dict[str, Any]:
    return {"peer_identity": {"key_ref": key_ref, "spki_sha256": list(pins)}}


class _Bus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        self.published.extend(events)
        return 0


class _Authority:
    def __init__(self, allows: bool = True) -> None:
        self.allows = allows

    def may_declare(self, principal: Any) -> bool:
        return self.allows


class _Gateway:
    def __init__(self, tmp_path, *, declared: frozenset[str] = frozenset(), groups: dict | None = None) -> None:
        self.configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
        self.writer = RepositoryFleetWriter(self.configs)
        self.fleet = InMemoryMcpServerRepository()
        self.bus = _Bus()
        self.authority = _Authority()
        common = {"repository": self.fleet, "event_bus": self.bus, "fleet_writer": self.writer}
        self.create = CreateMcpServerHandler(**common, peer_identity_authority=self.authority)
        self.update = UpdateMcpServerHandler(
            **common, declared_in_config=declared.__contains__, peer_identity_authority=self.authority, groups=groups
        )

    def row(self, name: str = "payments"):
        return asyncio.run(self.configs.get(name))

    def pinned(self, *pins: str, endpoint: str | None = ENDPOINT) -> McpServer:
        self.create.handle(
            CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=endpoint, tls=_tls(*pins))
        )
        return self.fleet.get("payments")


@pytest.fixture
def gateway(tmp_path) -> Iterator[_Gateway]:
    gateway = _Gateway(tmp_path)
    yield gateway
    gateway.writer.close()


def _update(**fields: Any) -> UpdateMcpServerCommand:
    return UpdateMcpServerCommand(mcp_server_id="payments", **fields)


class TestTheOperatorsSequence:
    def test_registered_before_the_pod_exists_without_an_endpoint(self, gateway) -> None:
        server = gateway.pinned(OLD, endpoint=None)

        assert server.upstream_identity() == "peer-key:lineage"
        assert gateway.row().endpoint is None

    def test_the_endpoint_is_set_cleared_and_set_again(self, gateway) -> None:
        server = gateway.pinned(OLD, endpoint=None)

        gateway.update.handle(_update(endpoint=ENDPOINT))
        assert gateway.row().endpoint == ENDPOINT and server._endpoint == ENDPOINT
        gateway.update.handle(_update(endpoint=""))
        assert gateway.row().endpoint is None
        gateway.update.handle(_update(endpoint=MOVED))
        assert gateway.row().endpoint == MOVED

    def test_a_new_address_starts_without_the_old_ones_failures(self, gateway) -> None:
        server = gateway.pinned(OLD)
        for _ in range(3):
            server.health.record_failure()

        gateway.update.handle(_update(endpoint=MOVED))

        assert server.health.consecutive_failures == 0 and server.health.can_retry()

    def test_the_update_is_published_for_the_other_replicas(self, gateway) -> None:
        gateway.pinned(OLD)

        gateway.update.handle(_update(endpoint=MOVED))

        assert any(isinstance(event, McpServerUpdated) for event in gateway.bus.published)


class TestThePermission:
    def test_the_endpoint_of_a_key_pinned_server_needs_it(self, gateway) -> None:
        gateway.pinned(OLD)
        gateway.authority.allows = False

        with pytest.raises(PeerIdentityChangeDenied):
            gateway.update.handle(_update(endpoint=MOVED))

        assert gateway.row().endpoint == ENDPOINT

    def test_a_plain_servers_endpoint_does_not(self, gateway) -> None:
        # The fleet projection applies it on every replica (#1955).
        gateway.create.handle(CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT))
        gateway.authority.allows = False

        gateway.update.handle(_update(endpoint=MOVED))

        assert gateway.row().endpoint == MOVED

    def test_without_durable_storage_no_endpoint_changes(self) -> None:
        fleet = InMemoryMcpServerRepository()
        create = CreateMcpServerHandler(fleet, _Bus())
        create.handle(CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT))

        with pytest.raises(ValidationError, match="needs a durable configuration store"):
            UpdateMcpServerHandler(fleet, _Bus()).handle(_update(endpoint=MOVED))

        assert fleet.get("payments")._endpoint == ENDPOINT


class TestATlsBlockFromACommand:
    """`verify_ssl` and `ca_cert_path` decide whose certificate is accepted; they stay file-only."""

    @pytest.mark.parametrize(
        "tls",
        [
            {"verify_ssl": False},
            {"ca_cert_path": "/etc/passwd"},
            {**_tls(OLD), "verify_ssl": False},
            {"max_connection_age": "1h"},
            {**_tls(OLD), "max_connection_age": "soon"},
            "x",
            ["peer_identity"],
        ],
    )
    def test_anything_but_a_peer_identity_and_its_age_is_refused(self, gateway, tls) -> None:
        with pytest.raises(ValidationError):
            gateway.create.handle(
                CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, tls=tls)
            )

        assert gateway.row() is None

    def test_an_update_cannot_set_them_either(self, gateway) -> None:
        gateway.pinned(OLD)

        with pytest.raises(ValidationError, match="only in the configuration file"):
            gateway.update.handle(_update(tls={**_tls(OLD), "ca_cert_path": "/tmp/mine.pem"}))

        assert gateway.row().tls == _tls(OLD)

    def test_a_peer_identity_with_its_age_is_accepted(self, gateway) -> None:
        gateway.create.handle(
            CreateMcpServerCommand(
                mcp_server_id="payments",
                mode="remote",
                endpoint=ENDPOINT,
                tls={**_tls(OLD), "max_connection_age": "30m"},
            )
        )

        assert gateway.row().tls["max_connection_age"] == "30m"


class TestARotation:
    def test_the_next_key_is_added_then_the_old_one_removed(self, gateway) -> None:
        server = gateway.pinned(OLD)

        gateway.update.handle(_update(tls=_tls(OLD, NEW)))
        gateway.update.handle(_update(tls=_tls(NEW)))

        assert server._peer_identity.spki_sha256 == frozenset({NEW})
        assert gateway.row().tls == _tls(NEW)

    def test_a_list_that_shares_no_key_is_refused(self, gateway) -> None:
        gateway.pinned(OLD)

        with pytest.raises(ValidationError, match="shares no key"):
            gateway.update.handle(_update(tls=_tls(NEW, NEXT)))

        assert gateway.row().tls == _tls(OLD)

    def test_a_peer_identity_is_not_removed(self, gateway) -> None:
        gateway.pinned(OLD)

        with pytest.raises(PeerKeyNameTaken):
            gateway.update.handle(_update(tls={}))


class TestADiscoveredServerGivenAPeerIdentity:
    """Section 4f: updated, not created again, and a human's from then on."""

    def test_it_becomes_key_pinned_human_and_recorded(self, gateway) -> None:
        gateway.create.handle(
            CreateMcpServerCommand(
                mcp_server_id="payments",
                mode="remote",
                endpoint=ENDPOINT,
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"203.0.113.5"}),
            )
        )

        gateway.update.handle(_update(tls=_tls(OLD)))

        server = gateway.fleet.get("payments")
        assert server.upstream_identity() == "peer-key:lineage"
        assert server._provenance is Provenance.HUMAN and server._runtime_addresses is None
        assert gateway.row().provenance is Provenance.HUMAN
        assert asyncio.run(gateway.configs.get_peer_key_names()) == {"payments": "lineage"}

    def test_its_endpoint_meets_the_ssrf_policy_now(self, gateway) -> None:
        gateway.create.handle(
            CreateMcpServerCommand(
                mcp_server_id="payments",
                mode="remote",
                endpoint="https://10.244.3.17:8443",
                provenance=Provenance.DISCOVERY,
                runtime_addresses=frozenset({"10.244.3.17"}),
            )
        )

        with pytest.raises(SsrfBlocked):
            gateway.update.handle(_update(tls=_tls(OLD)))


class TestWhatCannotChange:
    def test_a_server_the_file_declares(self, tmp_path) -> None:
        gateway = _Gateway(tmp_path, declared=frozenset({"payments"}))
        try:
            gateway.pinned(OLD)
            with pytest.raises(McpServerDeclaredInConfigError):
                gateway.update.handle(_update(endpoint=MOVED))
        finally:
            gateway.writer.close()

    def test_a_group_member_is_not_given_a_peer_identity(self, tmp_path) -> None:
        member = SimpleNamespace(get_member=lambda name: name == "payments")
        gateway = _Gateway(tmp_path, groups={"pool": member})
        try:
            gateway.create.handle(CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT))
            with pytest.raises(ValidationError, match="group member"):
                gateway.update.handle(_update(tls=_tls(OLD)))
        finally:
            gateway.writer.close()

    def test_a_key_pinned_server_does_not_join_a_group(self, gateway) -> None:
        gateway.pinned(OLD)
        handler = AddGroupMemberHandler(repository=gateway.fleet, groups={"pool": object()}, event_bus=gateway.bus)

        with pytest.raises(ValidationError, match="cannot join a group"):
            handler.handle(AddGroupMemberCommand(group_id="pool", mcp_server_id="payments"))


def test_the_core_advertises_the_capability(monkeypatch) -> None:
    from mcp_hangar.server.api import system

    metrics = SimpleNamespace(to_dict=lambda: {})
    monkeypatch.setattr(system, "dispatch_query", AsyncMock(return_value=metrics))

    response = asyncio.run(system.get_system_info(SimpleNamespace()))

    assert "peer_identity" in __import__("json").loads(response.body)["system"]["capabilities"]


# ---------------------------------------------------------------------------
# Through the real API router, with real API keys
# ---------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path) -> Iterator[tuple[TestClient, dict[str, str], InMemoryMcpServerRepository]]:
    from mcp_hangar.auth.bootstrap import bootstrap_auth
    from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.infrastructure.event_bus import EventBus
    from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
    from mcp_hangar.infrastructure.query_bus import QueryBus
    from mcp_hangar.server.api.router import create_api_router
    from mcp_hangar.server.context import init_context, reset_context

    roles = {"developer": "svc:dev", "kubernetes-operator": "svc:operator"}
    writer = RepositoryFleetWriter(InMemoryMcpServerConfigRepository())
    reset_context()
    try:
        components = bootstrap_auth(
            AuthConfig(
                enabled=True,
                allow_anonymous=False,
                api_key=ApiKeyAuthConfig(enabled=True),
                storage=StorageConfig(driver="memory", path=str(tmp_path / "auth.db")),
                role_assignments=[
                    RoleAssignment(principal=principal, role=role, scope="global") for role, principal in roles.items()
                ],
            )
        )
        keys = {
            role: components.api_key_store.create_key(principal_id=principal, name=role)
            for role, principal in roles.items()
        }
        runtime = create_runtime(
            event_bus=EventBus(event_store=InMemoryEventStore()), command_bus=CommandBus(), query_bus=QueryBus()
        )
        register_crud_handlers(
            runtime.command_bus,
            runtime.repository,
            runtime.event_bus,
            fleet_writer=writer,
            peer_identity_authority=ContextPeerIdentityAuthority(),
        )
        context = init_context(runtime)
        context.auth_components = components
        with TestClient(create_api_router(components)) as client:
            yield client, keys, runtime.repository
    finally:
        reset_context()
        writer.close()


@pytest.mark.security
class TestTheRestApiWithRealKeys:
    def _register(self, client, keys) -> None:
        response = client.post(
            "/mcp_servers",
            json={"mcp_server_id": "payments", "mode": "remote", "tls": _tls(OLD)},
            headers={"X-API-Key": keys["kubernetes-operator"]},
        )
        assert response.status_code == 201, response.text

    def test_the_operator_registers_and_moves_a_key_pinned_server(self, served) -> None:
        client, keys, fleet = served
        self._register(client, keys)

        response = client.put(
            "/mcp_servers/payments", json={"endpoint": ENDPOINT}, headers={"X-API-Key": keys["kubernetes-operator"]}
        )

        assert response.status_code == 200, response.text
        assert fleet.get("payments")._endpoint == ENDPOINT

    def test_a_developer_can_neither_register_one_nor_move_it(self, served) -> None:
        client, keys, fleet = served
        refused = client.post(
            "/mcp_servers",
            json={"mcp_server_id": "other", "mode": "remote", "tls": _tls(NEW, key_ref="other")},
            headers={"X-API-Key": keys["developer"]},
        )
        self._register(client, keys)
        moved = client.put(
            "/mcp_servers/payments", json={"endpoint": ENDPOINT}, headers={"X-API-Key": keys["developer"]}
        )

        assert (refused.status_code, moved.status_code) == (403, 403)
        assert not fleet.exists("other") and fleet.get("payments")._endpoint is None

    def test_only_the_operator_forgets_a_deleted_servers_name(self, served) -> None:
        client, keys, fleet = served
        self._register(client, keys)
        deleted = client.delete("/mcp_servers/payments", headers={"X-API-Key": keys["kubernetes-operator"]})

        refused = client.delete("/mcp_servers/payments/peer_identity", headers={"X-API-Key": keys["developer"]})
        forgotten = client.delete(
            "/mcp_servers/payments/peer_identity", headers={"X-API-Key": keys["kubernetes-operator"]}
        )

        assert (deleted.status_code, refused.status_code, forgotten.status_code) == (200, 403, 200), forgotten.text

    def test_a_developer_cannot_turn_off_verification_on_a_plain_server(self, served) -> None:
        client, keys, fleet = served
        created = client.post(
            "/mcp_servers",
            json={"mcp_server_id": "plain", "mode": "remote", "endpoint": ENDPOINT, "tls": {"verify_ssl": False}},
            headers={"X-API-Key": keys["developer"]},
        )
        malformed = client.post(
            "/mcp_servers",
            json={"mcp_server_id": "plain", "mode": "remote", "endpoint": ENDPOINT, "tls": "x"},
            headers={"X-API-Key": keys["developer"]},
        )

        assert (created.status_code, malformed.status_code) == (422, 422)
        assert not fleet.exists("plain")
