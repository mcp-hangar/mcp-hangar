"""Declaring or removing a peer identity needs `mcp_servers:peer_identity` (#1947, ADR-030 §9).

A key list decides which upstream a call may reach. `developer` holds
`mcp_servers:write` and `mcp_servers:lifecycle`, which register and delete a
server; neither may declare a key list or remove a server that has one, or a
delete followed by a plain re-create under the same name would downgrade it.
A discovery source never may, in any authentication mode.

The REST case runs through the real API router with real API keys: a per-request
control checked only against a mocked context has missed fail-opens before.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import (
    CreateMcpServerHandler,
    DeleteMcpServerHandler,
    register_crud_handlers,
)
from mcp_hangar.domain.exceptions import AccessDeniedError, AuthorizationError, ValidationError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.persistence.config_repository import InMemoryMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.server.bootstrap.peer_identity_authority import ContextPeerIdentityAuthority

PIN = "sha256:" + "a" * 64
TLS = {"peer_identity": {"key_ref": "lineage", "spki_sha256": [PIN]}}


class _Bus:
    def publish(self, event) -> None:
        pass

    def publish_aggregate_events(self, *args, **kwargs) -> int:
        return 0


class _Authority:
    def __init__(self, allows: bool) -> None:
        self.allows = allows
        self.asked: list[object] = []

    def may_declare(self, principal) -> bool:
        self.asked.append(principal)
        return self.allows


def _create(tls=TLS, **fields) -> CreateMcpServerCommand:
    return CreateMcpServerCommand(
        mcp_server_id="payments", mode="remote", endpoint="https://203.0.113.5:8443", tls=tls, **fields
    )


def _pinned() -> McpServer:
    return McpServer(mcp_server_id="payments", mode="remote", endpoint="https://10.0.0.5:8443", tls=dict(TLS))


class TestTheCreateHandler:
    def test_a_permitted_caller_declares_a_peer_identity(self) -> None:
        repository = InMemoryMcpServerRepository()
        writer = RepositoryFleetWriter(InMemoryMcpServerConfigRepository())
        handler = CreateMcpServerHandler(
            repository, _Bus(), fleet_writer=writer, peer_identity_authority=_Authority(True)
        )

        try:
            handler.handle(_create())
        finally:
            writer.close()

        assert repository.get("payments").upstream_identity() == "peer-key:lineage"

    def test_a_caller_without_the_permission_is_refused_and_nothing_is_created(self) -> None:
        repository = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(repository, _Bus(), peer_identity_authority=_Authority(False))

        with pytest.raises(AccessDeniedError, match="requires mcp_servers:peer_identity"):
            handler.handle(_create())

        assert repository.get("payments") is None

    def test_a_handler_wired_without_an_authority_refuses(self) -> None:
        with pytest.raises(AccessDeniedError):
            CreateMcpServerHandler(InMemoryMcpServerRepository(), _Bus()).handle(_create())

    def test_a_discovery_source_is_refused_even_when_everyone_is_allowed(self) -> None:
        authority = _Authority(True)
        handler = CreateMcpServerHandler(InMemoryMcpServerRepository(), _Bus(), peer_identity_authority=authority)

        with pytest.raises(AccessDeniedError, match="discovery source does not declare"):
            handler.handle(_create(provenance=Provenance.DISCOVERY))

        assert authority.asked == []

    def test_a_malformed_peer_identity_is_a_validation_error(self) -> None:
        handler = CreateMcpServerHandler(
            InMemoryMcpServerRepository(), _Bus(), peer_identity_authority=_Authority(True)
        )

        with pytest.raises(ValidationError, match="is not a key pin"):
            handler.handle(_create(tls={"peer_identity": {"key_ref": "k", "spki_sha256": ["nope"]}}))

    def test_a_server_without_a_peer_identity_does_not_ask(self) -> None:
        authority = _Authority(False)
        handler = CreateMcpServerHandler(InMemoryMcpServerRepository(), _Bus(), peer_identity_authority=authority)

        handler.handle(_create(tls=None))

        assert authority.asked == []


class TestTheDeleteHandler:
    def _handler(self, server: McpServer, authority) -> tuple[DeleteMcpServerHandler, InMemoryMcpServerRepository]:
        repository = InMemoryMcpServerRepository()
        repository.add(server.mcp_server_id, server)
        return DeleteMcpServerHandler(repository, _Bus(), peer_identity_authority=authority), repository

    def test_removing_a_key_pinned_server_needs_the_permission(self) -> None:
        handler, repository = self._handler(_pinned(), _Authority(False))

        with pytest.raises(AccessDeniedError, match="remove the peer identity of"):
            handler.handle(DeleteMcpServerCommand(mcp_server_id="payments"))

        assert repository.get("payments") is not None

    def test_a_discovery_source_never_removes_one(self) -> None:
        handler, repository = self._handler(_pinned(), _Authority(True))

        with pytest.raises(AccessDeniedError, match="discovery source"):
            handler.handle(DeleteMcpServerCommand(mcp_server_id="payments", provenance=Provenance.DISCOVERY))

        assert repository.get("payments") is not None

    def test_a_permitted_caller_removes_it(self) -> None:
        handler, repository = self._handler(_pinned(), _Authority(True))

        handler.handle(DeleteMcpServerCommand(mcp_server_id="payments"))

        assert repository.get("payments") is None

    def test_a_plain_server_is_deleted_without_asking(self) -> None:
        authority = _Authority(False)
        plain = McpServer(mcp_server_id="plain", mode="remote", endpoint="https://plain:8443")
        handler, repository = self._handler(plain, authority)

        handler.handle(DeleteMcpServerCommand(mcp_server_id="plain"))

        assert repository.get("plain") is None and authority.asked == []


def _context(enabled: bool, authorize=None):
    components = MagicMock(enabled=enabled)
    components.authz_middleware.authorize.side_effect = authorize
    return MagicMock(auth_components=components)


class TestTheAuthorityDecidesAsTheRoutesDo:
    def _decide(self, context, principal) -> bool:
        with patch("mcp_hangar.server.bootstrap.peer_identity_authority.get_context", return_value=context):
            return ContextPeerIdentityAuthority().may_declare(principal)

    def test_authentication_off_allows(self) -> None:
        assert self._decide(_context(enabled=False), None) is True

    def test_no_principal_is_refused_when_authentication_is_on(self) -> None:
        assert self._decide(_context(enabled=True), None) is False

    def test_an_anonymous_principal_is_refused(self) -> None:
        anonymous = MagicMock()
        anonymous.is_anonymous.return_value = True

        assert self._decide(_context(enabled=True), anonymous) is False

    def test_a_denial_from_the_authorizer_is_a_refusal(self) -> None:
        principal = MagicMock()
        principal.is_anonymous.return_value = False
        denied = _context(enabled=True, authorize=AuthorizationError("no", principal_id="p", action="a", resource="r"))

        assert self._decide(denied, principal) is False

    def test_an_allow_is_an_allow_for_the_peer_identity_action(self) -> None:
        principal = MagicMock()
        principal.is_anonymous.return_value = False
        context = _context(enabled=True)

        assert self._decide(context, principal) is True
        assert context.auth_components.authz_middleware.authorize.call_args.kwargs["action"] == "peer_identity"


# ---------------------------------------------------------------------------
# Through the real API router, with real API keys
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway(tmp_path) -> Iterator[tuple[TestClient, dict[str, str], InMemoryMcpServerRepository]]:
    from mcp_hangar.auth.bootstrap import bootstrap_auth
    from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.infrastructure.event_bus import EventBus
    from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
    from mcp_hangar.infrastructure.query_bus import QueryBus
    from mcp_hangar.server.api.router import create_api_router
    from mcp_hangar.server.context import init_context, reset_context

    roles = {"developer": "svc:dev", "kubernetes-operator": "svc:operator", "admin": "svc:admin"}
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
            peer_identity_authority=ContextPeerIdentityAuthority(),
        )
        context = init_context(runtime)
        context.auth_components = components
        with TestClient(create_api_router(components)) as client:
            yield client, keys, runtime.repository
    finally:
        reset_context()


@pytest.mark.security
class TestTheRestApiWithRealKeys:
    @pytest.mark.parametrize(("role", "status"), [("developer", 403), ("kubernetes-operator", 200), ("admin", 200)])
    def test_deleting_a_key_pinned_server(self, gateway, role: str, status: int) -> None:
        client, keys, repository = gateway
        repository.add("payments", _pinned())

        response = client.delete("/mcp_servers/payments", headers={"X-API-Key": keys[role]})

        assert response.status_code == status, response.text
        assert (repository.get("payments") is None) is (status == 200)

    def test_a_developer_still_deletes_a_plain_server(self, gateway) -> None:
        client, keys, repository = gateway
        repository.add("plain", McpServer(mcp_server_id="plain", mode="remote", endpoint="https://plain:8443"))

        response = client.delete("/mcp_servers/plain", headers={"X-API-Key": keys["developer"]})

        assert response.status_code == 200, response.text
        assert repository.get("plain") is None
