"""The operator's API key must be able to deliver egress policy without being admin.

The operator (mcp-hangar-operator, pkg/hangar/client.go) authenticates to core
with ``X-API-Key`` and makes exactly four kinds of call:

    GET    /api/mcp_servers/{id}
    GET    /api/mcp_servers/{id}/health
    GET    /api/mcp_servers/{id}/tools
    POST   /api/mcp_servers/{id}/l7_policy
    DELETE /api/mcp_servers/{id}/l7_policy

Two things make this a contract worth pinning rather than an incidental fact.

1. ``/l7_policy`` now requires ``policy:write`` rather than
   ``mcp_servers:write``, because ``mcp_servers:write`` is held by
   ``developer`` and ADR-013 makes the compiled-policy channel privileged. If
   no non-admin role holds ``policy:write``, every operator deployment has to
   run with an admin key -- or, worse, silently stops delivering policy while
   the CRD still reports ``Compiled``.
2. ``provider-admin`` holds only the pre-rename ``provider:*`` permissions,
   which the REST API authorizes against nothing. Before this it failed every
   call in the list above, including the reads.

So ``provider-admin`` is the documented least-privilege home for an operator
key, and these tests fail if that stops being true -- in either direction.
"""

import pytest

from mcp_hangar.auth.roles import BUILTIN_ROLES
from mcp_hangar.server.api.route_permissions import resolve_rule


OPERATOR_CALLS = [
    ("GET", "/mcp_servers/srv1"),
    ("GET", "/mcp_servers/srv1/health"),
    ("GET", "/mcp_servers/srv1/tools"),
    ("POST", "/mcp_servers/srv1/l7_policy"),
    ("DELETE", "/mcp_servers/srv1/l7_policy"),
]


def _permits(role_name: str, method: str, path: str) -> bool:
    rule = resolve_rule(method, path)
    assert rule is not None, f"{method} {path} is not in the permission table"
    if rule.permission is None:
        return True
    resource_type, action = rule.permission
    return BUILTIN_ROLES[role_name].has_permission(resource_type, action, "*")


class TestProviderAdminCanRunTheOperator:
    @pytest.mark.parametrize("method,path", OPERATOR_CALLS)
    def test_every_operator_call_is_permitted(self, method, path):
        assert _permits("provider-admin", method, path), (
            f"provider-admin cannot {method} {path}; an operator key would have to be admin"
        )

    def test_admin_also_works(self):
        """The escape hatch stays open for deployments already using admin."""
        assert all(_permits("admin", m, p) for m, p in OPERATOR_CALLS)


class TestProviderAdminStaysLeastPrivilege:
    """Granting the operator its set must not quietly widen the role."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("DELETE", "/mcp_servers/srv1"),  # deleting a server
            ("PUT", "/mcp_servers/srv1"),  # reconfiguring one
            ("POST", "/mcp_servers"),  # creating one
            ("POST", "/mcp_servers/srv1/start"),  # lifecycle
            ("POST", "/mcp_servers/srv1/stop"),
            ("POST", "/config/reload"),  # re-applying governance inputs
            ("POST", "/auth/roles/assign"),  # self-escalation
        ],
    )
    def test_capabilities_the_operator_does_not_use_stay_denied(self, method, path):
        assert not _permits("provider-admin", method, path), (
            f"provider-admin gained {method} {path}, which the operator never calls"
        )


class TestDeveloperStaysOutOfTheEgressChannel:
    """The point of moving /l7_policy to policy:write in the first place."""

    def test_developer_cannot_set_egress_policy(self):
        assert not _permits("developer", "POST", "/mcp_servers/srv1/l7_policy")

    def test_developer_cannot_clear_egress_policy(self):
        assert not _permits("developer", "DELETE", "/mcp_servers/srv1/l7_policy")

    def test_developer_keeps_its_own_server_capabilities(self):
        """The narrowing was surgical, not a demotion of the whole role."""
        assert _permits("developer", "DELETE", "/mcp_servers/srv1")
        assert _permits("developer", "POST", "/mcp_servers/srv1/start")


class TestReadOnlyRolesStayReadOnly:
    def test_viewer_cannot_touch_egress_policy(self):
        assert not _permits("viewer", "POST", "/mcp_servers/srv1/l7_policy")

    def test_auditor_cannot_touch_egress_policy(self):
        assert not _permits("auditor", "POST", "/mcp_servers/srv1/l7_policy")
