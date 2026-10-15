"""The built-in `kubernetes-operator` role holds exactly what the operator calls (#1827).

Since 0.17.26 the operator registers remote servers (`POST /api/mcp_servers/`),
starts and removes them and delivers compiled egress policy. That needs
`mcp_servers:write`, `mcp_servers:lifecycle` and `policy:write`, which only
`admin` held together; `developer` lacks the policy push and `provider-admin`
lacks registration. So every operator ran with a full admin key.

The permission set is pinned: a later addition is a decision, not a drift.
"""

from __future__ import annotations

import pytest

from mcp_hangar.auth.roles import BUILTIN_ROLES, ROLE_KUBERNETES_OPERATOR
from mcp_hangar.server.api.route_permissions import resolve_rule

#: Every call mcp-hangar-operator makes (pkg/hangar/client.go).
OPERATOR_CALLS = [
    ("GET", "/mcp_servers"),
    ("GET", "/mcp_servers/srv1"),
    ("GET", "/mcp_servers/srv1/health"),
    ("GET", "/mcp_servers/srv1/tools"),
    ("POST", "/mcp_servers"),
    ("POST", "/mcp_servers/srv1/start"),
    ("POST", "/mcp_servers/srv1/stop"),
    ("DELETE", "/mcp_servers/srv1"),
    ("POST", "/mcp_servers/srv1/l7_policy"),
    ("DELETE", "/mcp_servers/srv1/l7_policy"),
]

#: Things the operator never does and a compromised key must not be able to.
DENIED = [
    ("POST", "/groups"),
    ("POST", "/discovery/approve/srv1"),
    ("POST", "/config/reload"),
    ("POST", "/auth/roles/assign"),
    ("POST", "/auth/keys"),
    ("GET", "/auth/keys"),
]


def _permits(method: str, path: str) -> bool:
    rule = resolve_rule(method, path)
    assert rule is not None, f"{method} {path} is not in the permission table"
    if rule.permission is None:
        return True
    resource_type, action = rule.permission
    return ROLE_KUBERNETES_OPERATOR.has_permission(resource_type, action, "*")


def test_the_role_is_built_in():
    assert BUILTIN_ROLES["kubernetes-operator"] is ROLE_KUBERNETES_OPERATOR


def test_the_permission_set_is_pinned():
    held = {(p.resource_type, p.action) for p in ROLE_KUBERNETES_OPERATOR.permissions}
    assert held == {
        ("mcp_servers", "read"),
        ("mcp_servers", "write"),
        ("mcp_servers", "lifecycle"),
        ("policy", "read"),
        ("policy", "write"),
    }


@pytest.mark.parametrize(("method", "path"), OPERATOR_CALLS)
def test_every_operator_call_is_permitted(method, path):
    assert _permits(method, path), f"kubernetes-operator cannot {method} {path}"


@pytest.mark.parametrize(("method", "path"), DENIED)
def test_nothing_else_is_permitted(method, path):
    assert not _permits(method, path), f"kubernetes-operator can {method} {path}"


def test_it_holds_no_wildcard_and_no_tool_invocation():
    assert not ROLE_KUBERNETES_OPERATOR.has_permission("tool", "invoke", "*")
    assert not ROLE_KUBERNETES_OPERATOR.has_permission("*", "*", "*")
