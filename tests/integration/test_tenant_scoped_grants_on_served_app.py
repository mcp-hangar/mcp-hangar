"""Tenant-scoped role grants on the app ``serve --http`` builds, end to end.

A role bound at ``tenant:<id>`` used to open every route it named for every
tenant: the authorizer honoured it as if it were global, and each route checks
its permission against ``resource_id="*"``. These are the regression tests for
the three routes where that was confirmed, plus the fail-closed rule for every
other route.

The stack is the real one: API keys minted in the real key store, role
assignments applied from config by ``bootstrap_auth`` (memory and SQLite role
stores), the real ``RBACAuthorizer``, and ``create_api_router`` mounted at
``/api`` inside ``create_auth_enforced_app`` -- the assembly
``test_rest_authz_on_served_app.py`` mirrors from ``lifecycle.py``. Nothing in
the auth path is mocked, because that is where the failure lived.

Each confirmed route is asserted three ways: the tenant-scoped holder is
confined or refused, the global holder is unchanged, and a principal without
the permission is refused.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mcp_hangar.application.queries import register_all_handlers as register_query_handlers
from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.domain.events import ToolWithdrawn
from mcp_hangar.domain.events.invocation import ToolInvocationCompleted, ToolInvocationFailed
from mcp_hangar.domain.value_objects.security import Permission, Role
from mcp_hangar.infrastructure.event_bus import EventBus, get_event_bus, reset_event_bus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app
from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS
from mcp_hangar.server.api.ws.manager import connection_manager
from mcp_hangar.server.context import init_context, reset_context
from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

SCOPE_REFUSAL = "tenant-scoped grant; route requires a global grant"
SERVER = "srv-shared"


def _served_app(auth_components):
    """The ASGI stack ServerLifecycle.run_http serves (see test_rest_authz_on_served_app.py)."""
    api_app = create_api_router(auth_components=auth_components)

    async def live(_request):
        return JSONResponse({"status": "ok"})

    aux_app = Starlette(routes=[Route("/health/live", live, methods=["GET"]), Mount("/api", app=api_app)])

    async def mcp_app(scope, receive, send):
        await JSONResponse({"surface": "mcp"})(scope, receive, send)

    async def combined_app(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path.startswith("/health/") or path == "/api" or path.startswith("/api/"):
                await aux_app(scope, receive, send)
                return
        await mcp_app(scope, receive, send)

    return create_auth_enforced_app(combined_app, auth_components)


def _bootstrap(tmp_path: Path, bindings: dict[str, tuple[str, str, str | None]], driver: str = "memory"):
    """Real auth: ``{name: (role, scope, key tenant)}`` -> components and one API key per name."""
    config = AuthConfig(
        enabled=True,
        allow_anonymous=False,
        api_key=ApiKeyAuthConfig(enabled=True),
        storage=StorageConfig(driver=driver, path=str(tmp_path / "auth.db")),
        role_assignments=[
            RoleAssignment(principal=f"svc:{name}", role=role, scope=scope)
            for name, (role, scope, _) in bindings.items()
        ],
    )
    components = bootstrap_auth(config)
    keys = {
        name: components.api_key_store.create_key(principal_id=f"svc:{name}", name="k", tenant_id=tenant)
        for name, (_role, _scope, tenant) in bindings.items()
    }
    return components, keys


def _wire_context(components, event_store):
    """A real ApplicationContext: query handlers over ``event_store``, auth wired."""
    runtime = create_runtime(event_bus=EventBus(event_store=event_store), query_bus=QueryBus())
    register_query_handlers(runtime.query_bus, runtime.repository, event_store=event_store)
    ctx = init_context(runtime)
    ctx.auth_components = components
    return ctx


def _identity(tenant: str | None, user: str) -> dict[str, object]:
    return {"user_id": user, "principal_type": "service", "tenant_id": tenant, "correlation_id": f"corr-{user}"}


def _tenant_b_failure(server: str = SERVER) -> ToolInvocationFailed:
    return ToolInvocationFailed(
        mcp_server_id=server,
        tool_name="query_customers",
        correlation_id="corr-b",
        error_message="upstream said: row 42 for customer tenant-b is locked",
        error_type="UpstreamError",
        identity_context=_identity("B", "svc:bob"),
    )


def _tenant_a_completion(server: str = SERVER) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(
        mcp_server_id=server, tool_name="add", correlation_id="corr-a", identity_context=_identity("A", "svc:alice")
    )


def _anonymous_completion(server: str = SERVER) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id=server, tool_name="add", correlation_id="corr-anon")


def _reason(response) -> str | None:
    return ((response.json().get("error") or {}).get("details") or {}).get("reason")


@pytest.fixture(autouse=True)
def _fresh_state():
    reset_event_bus()
    reset_tool_projection_registry()
    yield
    reset_context()
    reset_tool_projection_registry()
    reset_event_bus()


# =====================================================================
# 1. /ws/events -- audit:read
# =====================================================================


def _stream(client: TestClient, key: str, subscribe: dict, publish: list, receive: int) -> list[dict]:
    """Connect, subscribe, publish *publish* on the bus, return the next *receive* messages."""
    before = connection_manager.active_count
    with client.websocket_connect("/api/ws/events", headers={"X-API-Key": key}) as ws:
        ws.send_json(subscribe)
        assert ws.receive_json()["type"] == "subscribed"
        # The ack goes out before the bus subscription; registration follows it.
        deadline = time.monotonic() + 5
        while connection_manager.active_count <= before and time.monotonic() < deadline:
            time.sleep(0.01)
        for event in publish:
            get_event_bus().publish(event)
        return [ws.receive_json() for _ in range(receive)]


@pytest.mark.parametrize("driver", ["memory", "sqlite"])
class TestTheEventStream:
    @pytest.fixture()
    def stack(self, driver, tmp_path):
        components, keys = _bootstrap(
            tmp_path,
            {
                "auditor-a": ("auditor", "tenant:A", "A"),
                "auditor-global": ("auditor", "global", None),
                "viewer-a": ("viewer", "tenant:A", "A"),
            },
            driver=driver,
        )
        return TestClient(_served_app(components), raise_server_exceptions=False), keys

    def test_a_tenant_scoped_auditor_receives_only_its_tenant(self, stack):
        """Tenant B's event and the tenantless one are never delivered; tenant A's arrives first."""
        client, keys = stack
        events = [_tenant_b_failure(), ToolWithdrawn(tenant_id=None, mcp_server=SERVER, tool="t", kind="tool")]

        [first] = _stream(client, keys["auditor-a"], {"type": "subscribe"}, [*events, _tenant_a_completion()], 1)

        assert first["event_type"] == "ToolInvocationCompleted"
        assert first["identity_context"]["tenant_id"] == "A"

    def test_a_client_filter_cannot_widen_the_tenant(self, stack):
        """Asking for tenant B's server by id is a filter over the tenant's events, not around them."""
        client, keys = stack
        subscribe = {"type": "subscribe", "mcp_server_ids": ["srv-b", "srv-a"]}

        [first] = _stream(
            client, keys["auditor-a"], subscribe, [_tenant_b_failure("srv-b"), _tenant_a_completion("srv-a")], 1
        )

        assert first["mcp_server_id"] == "srv-a"
        assert first["identity_context"]["tenant_id"] == "A"

    def test_a_global_auditor_still_receives_every_tenant(self, stack):
        client, keys = stack
        events = [
            _tenant_b_failure(),
            ToolWithdrawn(tenant_id=None, mcp_server=SERVER, tool="t", kind="tool"),
            _tenant_a_completion(),
        ]

        received = _stream(client, keys["auditor-global"], {"type": "subscribe"}, events, 3)

        assert [m["event_type"] for m in received] == [
            "ToolInvocationFailed",
            "ToolWithdrawn",
            "ToolInvocationCompleted",
        ]
        assert received[0]["identity_context"]["tenant_id"] == "B"

    def test_a_principal_without_audit_read_is_refused(self, stack):
        client, keys = stack

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/api/ws/events", headers={"X-API-Key": keys["viewer-a"]}) as ws:
                ws.send_json({"type": "subscribe"})
                ws.receive_json()

        assert excinfo.value.code == 1008


# =====================================================================
# 2. GET /mcp_servers/{id}/tools/history -- mcp_servers:read
# =====================================================================


class TestTheInvocationHistory:
    @pytest.fixture()
    def client_and_keys(self, tmp_path):
        components, keys = _bootstrap(
            tmp_path,
            {
                "viewer-a": ("viewer", "tenant:A", "A"),
                "viewer-global": ("viewer", "global", None),
                "auditor-a": ("auditor", "tenant:A", "A"),
            },
        )
        store = InMemoryEventStore()
        # Tenant B first, so a limit applied before the tenant filter would
        # hand tenant A an empty page.
        store.append(
            stream_id_for(MCP_SERVER, SERVER),
            [_tenant_b_failure(), _anonymous_completion(), _tenant_a_completion()],
            expected_version=-1,
        )
        _wire_context(components, store)
        return TestClient(_served_app(components), raise_server_exceptions=False), keys

    def _history(self, client, key, query: str = ""):
        return client.get(f"/api/mcp_servers/{SERVER}/tools/history{query}", headers={"X-API-Key": key})

    def test_a_tenant_scoped_viewer_reads_only_its_tenant(self, client_and_keys):
        client, keys = client_and_keys

        response = self._history(client, keys["viewer-a"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert [row["identity_context"]["tenant_id"] for row in body["history"]] == ["A"]
        assert "tenant-b" not in response.text

    def test_the_tenant_filter_runs_before_the_limit(self, client_and_keys):
        client, keys = client_and_keys

        body = self._history(client, keys["viewer-a"], "?limit=1").json()

        assert [row["identity_context"]["tenant_id"] for row in body["history"]] == ["A"]

    def test_a_global_viewer_still_reads_every_tenant(self, client_and_keys):
        client, keys = client_and_keys

        body = self._history(client, keys["viewer-global"]).json()

        assert body["total"] == 3
        assert "tenant-b" in body["history"][0]["error_message"]

    def test_a_principal_without_the_permission_is_refused(self, client_and_keys):
        client, keys = client_and_keys

        assert self._history(client, keys["auditor-a"]).status_code == 403


# =====================================================================
# 3. POST /admin/tools/{server}/{tool}/withdraw|restore -- mcp_servers:lifecycle
# =====================================================================


class TestWithdrawAndRestore:
    @pytest.fixture()
    def client_and_keys(self, tmp_path):
        components, keys = _bootstrap(
            tmp_path,
            {
                "dev-a": ("developer", "tenant:A", "A"),
                "dev-global": ("developer", "global", None),
                "admin": ("admin", "global", None),
                "viewer-a": ("viewer", "tenant:A", "A"),
            },
        )
        _wire_context(components, InMemoryEventStore())
        return TestClient(_served_app(components), raise_server_exceptions=False), keys

    @staticmethod
    def _post(client, key, verb: str, tool: str, body: dict | None = None):
        return client.post(f"/api/admin/tools/{SERVER}/{tool}/{verb}", headers={"X-API-Key": key}, json=body or {})

    @staticmethod
    def _withdrawn(tool: str) -> dict[str | None, bool]:
        registry = get_tool_projection_registry()
        return {tenant: registry.is_withdrawn(SERVER, tool, tenant_id=tenant) for tenant in ("A", "B", "C", None)}

    def test_a_tenant_scoped_developer_cannot_withdraw_for_another_tenant(self, client_and_keys):
        client, keys = client_and_keys

        response = self._post(client, keys["dev-a"], "withdraw", "danger", {"tenant_id": "B"})

        assert response.status_code == 403, response.text
        assert _reason(response) == "tenant-scoped grant; cannot act on another tenant"
        assert self._withdrawn("danger") == {"A": False, "B": False, "C": False, None: False}

    def test_no_tenant_means_its_own_tenant_not_every_tenant(self, client_and_keys):
        client, keys = client_and_keys

        response = self._post(client, keys["dev-a"], "withdraw", "danger")

        assert response.status_code == 200, response.text
        assert response.json()["tenant_id"] == "A"
        assert self._withdrawn("danger") == {"A": True, "B": False, "C": False, None: False}

    def test_it_withdraws_and_restores_within_its_own_tenant(self, client_and_keys):
        client, keys = client_and_keys

        assert self._post(client, keys["dev-a"], "withdraw", "danger", {"tenant_id": "A"}).status_code == 200
        assert self._withdrawn("danger")["A"] is True

        restored = self._post(client, keys["dev-a"], "restore", "danger")
        assert restored.status_code == 200, restored.text
        assert restored.json()["tenant_id"] == "A"
        assert self._withdrawn("danger")["A"] is False

    def test_it_cannot_restore_for_another_tenant(self, client_and_keys):
        client, keys = client_and_keys
        assert self._post(client, keys["admin"], "withdraw", "danger", {"tenant_id": "B"}).status_code == 200

        response = self._post(client, keys["dev-a"], "restore", "danger", {"tenant_id": "B"})

        assert response.status_code == 403
        assert self._withdrawn("danger")["B"] is True

    @pytest.mark.parametrize("body", [None, {"tenant_id": "A"}])
    def test_it_cannot_lift_an_admins_all_tenant_withdrawal(self, client_and_keys, body):
        client, keys = client_and_keys
        assert self._post(client, keys["admin"], "withdraw", "danger").status_code == 200

        response = self._post(client, keys["dev-a"], "restore", "danger", body)

        assert response.status_code == 403, response.text
        assert _reason(response) == "tenant-scoped grant; an all-tenant withdrawal needs a global grant to restore"
        assert self._withdrawn("danger") == {"A": True, "B": True, "C": True, None: True}

        assert self._post(client, keys["admin"], "restore", "danger").status_code == 200
        assert self._withdrawn("danger") == {"A": False, "B": False, "C": False, None: False}

    def test_a_global_developer_is_unchanged(self, client_and_keys):
        """No body still means every tenant -- for a grant that reaches every tenant."""
        client, keys = client_and_keys

        response = self._post(client, keys["dev-global"], "withdraw", "danger")
        assert response.status_code == 200 and response.json()["tenant_id"] is None
        assert self._withdrawn("danger") == {"A": True, "B": True, "C": True, None: True}

        assert self._post(client, keys["dev-global"], "restore", "danger").status_code == 200
        assert self._withdrawn("danger") == {"A": False, "B": False, "C": False, None: False}

        assert self._post(client, keys["dev-global"], "withdraw", "danger", {"tenant_id": "B"}).status_code == 200
        assert self._withdrawn("danger")["B"] is True

    def test_a_principal_without_lifecycle_is_refused(self, client_and_keys):
        client, keys = client_and_keys

        assert self._post(client, keys["viewer-a"], "withdraw", "danger", {"tenant_id": "A"}).status_code == 403
        assert self._withdrawn("danger")["A"] is False


# =====================================================================
# Every other route: a global grant is required
# =====================================================================


def _requests_for(rule) -> list[tuple[str, str]]:
    methods_part, template = rule.template.split(" ", 1)
    path = re.sub(r"\{[a-zA-Z_][a-zA-Z_0-9]*(:path)?\}", "x1", template).replace("/**", "/roles")
    methods = ["GET", "POST"] if methods_part == "ANY" else sorted(rule.methods or ())
    return [(method, path) for method in methods]


def _principal_for(permission: tuple[str, str]) -> str:
    return "holds-" + "-".join(permission).replace("*", "all")


# `getattr`: a table without the flag (before it existed) reads as "no rule is
# tenant-aware", which is what that table meant -- so this collects, and fails,
# against the code it guards against.
_FLEET_WIDE_REQUESTS = [
    pytest.param(rule.permission, method, path, id=f"{method} {path}")
    for rule in ROUTE_PERMISSIONS
    if rule.permission is not None and not getattr(rule, "tenant_aware", False)
    for method, path in _requests_for(rule)
]


@pytest.fixture(scope="module")
def fleet_stack(tmp_path_factory):
    """One principal per permission in the table, holding exactly it at ``tenant:A``."""
    components, _ = _bootstrap(tmp_path_factory.mktemp("fleet"), {})
    keys: dict[tuple[str, str], str] = {}
    for permission in {rule.permission for rule in ROUTE_PERMISSIONS if rule.permission is not None}:
        name = _principal_for(permission)
        components.role_store.add_role(
            Role(name=name, permissions=frozenset({Permission(resource_type=permission[0], action=permission[1])}))
        )
        components.role_store.assign_role(f"svc:{name}", name, scope="tenant:A")
        keys[permission] = components.api_key_store.create_key(principal_id=f"svc:{name}", name="k", tenant_id="A")
    return TestClient(_served_app(components), raise_server_exceptions=False), keys


@pytest.mark.parametrize(("permission", "method", "path"), _FLEET_WIDE_REQUESTS)
def test_every_fleet_wide_route_refuses_a_tenant_scoped_grant(fleet_stack, permission, method, path):
    """Table-driven over ROUTE_PERMISSIONS: a rule added later is covered without being named.

    The principal holds exactly the permission the rule asks for, bound at
    ``tenant:A``, so the only thing between it and the handler is the scope of
    its grant. The refusal comes from the guard, before any handler runs.
    """
    client, keys = fleet_stack
    body = {} if method in {"POST", "PUT", "PATCH"} else None

    response = client.request(method, f"/api{path}", headers={"X-API-Key": keys[permission]}, json=body)

    assert response.status_code == 403, response.text
    assert _reason(response) == SCOPE_REFUSAL


def test_a_tenant_aware_route_lets_the_same_kind_of_grant_through(fleet_stack):
    """The contrast: a tenant-aware rule is not refused by the guard (approvals confine themselves)."""
    client, keys = fleet_stack

    response = client.get("/api/approvals", headers={"X-API-Key": keys[("approval", "read")]})

    assert response.status_code != 403, response.text


# =====================================================================
# Approvals -- tenant-aware, and an approval naming no tenant is fleet business
# =====================================================================


class _ApprovalRepository:
    """In-memory store with the approval repository's surface."""

    def __init__(self) -> None:
        self.store: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self.store[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.store.get(approval_id)

    async def list_pending(self, mcp_server_id: str | None = None) -> list[ApprovalRequest]:
        return [r for r in self.store.values() if r.state == ApprovalState.PENDING]

    async def list_by_state(self, state, mcp_server_id: str | None = None) -> list[ApprovalRequest]:
        return [r for r in self.store.values() if r.state == state]

    async def update_state(self, approval_id, state, decided_by, decided_at, reason) -> None:
        approval = self.store.get(approval_id)
        if approval is not None:
            approval.state, approval.decided_by, approval.decided_at, approval.reason = (
                state,
                decided_by,
                decided_at,
                reason,
            )


class _Events:
    def publish(self, _event) -> None:
        return None


def _pending(approval_id: str, tenant: str | None) -> ApprovalRequest:
    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id=approval_id,
        mcp_server_id="grafana",
        tool_name="delete_dashboard",
        arguments={},
        arguments_hash="sha256:test",
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
        state=ApprovalState.PENDING,
        channel="noop",
        tenant_id=tenant,
    )


class TestApprovals:
    """A tenant-scoped approver acts on its own tenant's approvals, and on nothing tenantless."""

    @pytest.fixture()
    def stack(self, tmp_path):
        components, keys = _bootstrap(tmp_path, {"viewer-a": ("viewer", "tenant:A", "A")})
        components.role_store.add_role(
            Role(
                name="approver",
                permissions=frozenset(
                    {Permission(resource_type="approval", action="read"), Permission("approval", "resolve")}
                ),
            )
        )
        for name, scope, tenant in (
            ("approver-a", "tenant:A", "A"),
            ("approver-global", "global", None),
            ("approver-global-a", "global", "A"),
        ):
            components.role_store.assign_role(f"svc:{name}", "approver", scope=scope)
            keys[name] = components.api_key_store.create_key(principal_id=f"svc:{name}", name="k", tenant_id=tenant)

        repo = _ApprovalRepository()
        for approval_id, tenant in (("ap-fleet", None), ("ap-a", "A"), ("ap-b", "B")):
            repo.store[approval_id] = _pending(approval_id, tenant)
        service = ApprovalGateService(
            repository=repo,
            hold_registry=ApprovalHoldRegistry(),
            event_bus=_Events(),
            delivery=NoOpApprovalDelivery(),
        )
        _wire_context(components, InMemoryEventStore()).approval_gate = service
        return TestClient(_served_app(components), raise_server_exceptions=False), keys, repo

    @staticmethod
    def _listed(client, key) -> set[str]:
        response = client.get("/api/approvals", headers={"X-API-Key": key})
        assert response.status_code == 200, response.text
        return {approval["approval_id"] for approval in response.json()}

    @staticmethod
    def _get(client, key, approval_id: str):
        return client.get(f"/api/approvals/{approval_id}", headers={"X-API-Key": key})

    @staticmethod
    def _resolve(client, key, approval_id: str):
        return client.post(
            f"/api/approvals/{approval_id}/resolve", headers={"X-API-Key": key}, json={"decision": "approve"}
        )

    def test_a_tenant_scoped_approver_is_not_shown_a_tenantless_approval(self, stack):
        client, keys, _repo = stack

        assert self._listed(client, keys["approver-a"]) == {"ap-a"}
        assert self._get(client, keys["approver-a"], "ap-fleet").status_code == 404
        assert self._get(client, keys["approver-a"], "ap-b").status_code == 404

    def test_a_tenant_scoped_approver_cannot_resolve_a_tenantless_approval(self, stack):
        client, keys, repo = stack

        response = self._resolve(client, keys["approver-a"], "ap-fleet")

        assert response.status_code == 404, response.text
        assert repo.store["ap-fleet"].state == ApprovalState.PENDING

    def test_a_tenant_scoped_approver_still_serves_its_own_tenant(self, stack):
        client, keys, repo = stack

        assert self._get(client, keys["approver-a"], "ap-a").status_code == 200
        response = self._resolve(client, keys["approver-a"], "ap-a")

        # No waiter holds the call, so releasing the hold reports 409 after the
        # decision is already durable (see test_approval_resolve_authz.py): the
        # recorded state is what is asserted.
        assert response.status_code in (200, 409), response.text
        assert repo.store["ap-a"].state == ApprovalState.APPROVED

    def test_a_global_approver_is_unchanged(self, stack):
        """A global grant keeps the view it had: tenantless approvals, plus its own tenant's."""
        client, keys, repo = stack

        assert self._listed(client, keys["approver-global"]) == {"ap-fleet"}
        assert self._listed(client, keys["approver-global-a"]) == {"ap-fleet", "ap-a"}
        assert self._get(client, keys["approver-global"], "ap-fleet").status_code == 200

        response = self._resolve(client, keys["approver-global"], "ap-fleet")
        assert response.status_code in (200, 409), response.text
        assert repo.store["ap-fleet"].state == ApprovalState.APPROVED

    def test_a_principal_without_the_permission_is_refused(self, stack):
        client, keys, _repo = stack

        assert client.get("/api/approvals", headers={"X-API-Key": keys["viewer-a"]}).status_code == 403
