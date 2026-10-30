"""The REST routes the reachability gate drives through a real boot, in order (#1786).

``test_security_predicates_are_reachable.py`` probes a read-mostly set of routes
in its own process, against an app it composes. The routes here change the fleet:
servers, groups, discovery sources, credentials, roles, configuration. They need
every handler ``bootstrap()`` registers, a discovery registry and a configuration
file, and what they change must not reach the next test. So they run in
``_predicate_reachability_harness.py``, one process per run, and each probe here
is one request with one spy recording.

A probe is data, so that a probe, its row in ``_security_predicates.py`` and the
route-coverage check cannot disagree about a route's name. Order matters: a probe
acts on what the ones before it left behind, and ``{name}`` in a path is the field
of that name in an earlier probe's response (``keeps``).

``EXEMPT_ROUTES`` is the other half of the coverage check: a route with no probe
is listed there with the reason, or the check fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS, RouteRule, resolve_rule

__all__ = [
    "EXEMPT_ROUTES",
    "ROUTE_PERMISSIONS",
    "ROUTE_PROBES",
    "ROUTE_PROBE_NAMES",
    "RouteProbe",
    "RouteRule",
    "rule_of",
]

UPSTREAM = "upstream"
#: The principal `/auth/**` probes assign a role to and revoke it from. Never a probing caller: their
#: grants are what every later probe is authorized by.
TARGET_PRINCIPAL = "svc:probe-target"
#: Registered and removed here. Docker, so a create is a local launch, and it is never started.
SCRATCH_SERVER = "scratch"
#: Registered with a peer identity and deleted, which leaves its name on the record.
PINNED_SERVER = "pinned"
GROUP = "probe-group"
ROLE = "probe-role"
#: Seeded by the harness, pending, in the tenant of the probing caller.
APPROVAL = "approval-probe"
#: Directory the registered filesystem source scans, relative to the harness's working directory.
DISCOVERY_DIR = "discovered"
#: Definitions the harness writes there, to wait for approval.
TO_APPROVE, TO_REJECT = "found-one", "found-two"


@dataclass(frozen=True)
class RouteProbe:
    """One REST request."""

    method: str
    #: Below `/api`. `{name}` is filled from what an earlier probe kept.
    path: str
    body: dict[str, Any] = field(default_factory=dict)
    #: Who sends it: the fleet-wide caller, or the one confined to its tenant.
    caller: str = "fleet"
    #: Fields of the response that later probes name in their path.
    keeps: tuple[str, ...] = ()
    #: The action the handler itself asks `_check_permission` about, where it
    #: asks; the last one asked, where it asks twice.
    handler_asks: str | None = None
    #: Requests sent first, unrecorded, for what the route needs to exist. Each must be served.
    first: tuple[RouteProbe, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.method} /api{self.path}" + (" (tenant)" if self.caller == "tenant" else "")

    @property
    def rule(self) -> RouteRule | None:
        return rule_of(self.method, self.path)


def rule_of(method: str, path: str) -> RouteRule | None:
    """The rule governing a request for *path* below `/api`, query aside, as the guard resolves it."""
    return resolve_rule(method, path.split("?", 1)[0])


_SERVER = f"/mcp_servers/{SCRATCH_SERVER}"
_L7 = f"{_SERVER}/l7_policy"
_GROUP = f"/groups/{GROUP}"
_SOURCE = "/discovery/sources/{source_id}"
_APPROVAL = f"/approvals/{APPROVAL}"
_ROLE = f"/auth/roles/{ROLE}"
_POLICY = f"/auth/policies/provider/{UPSTREAM}"
_ASSIGNMENT = {"principal_id": TARGET_PRINCIPAL, "role_name": "viewer"}
_ALLOW_ALL = {"defaultAction": "allow"}
_PEER_IDENTITY = {"peer_identity": {"key_ref": "lineage", "spki_sha256": ["sha256:" + "a" * 64]}}
_PINNED = (
    RouteProbe("POST", "/mcp_servers", {"mcp_server_id": PINNED_SERVER, "mode": "remote", "tls": _PEER_IDENTITY}),
    RouteProbe("DELETE", f"/mcp_servers/{PINNED_SERVER}"),
)

ROUTE_PROBES: tuple[RouteProbe, ...] = (
    RouteProbe("GET", "/system"),
    # --- servers: the one upstream, then one registered here and removed.
    RouteProbe("GET", f"/mcp_servers/{UPSTREAM}/health", handler_asks="read"),
    RouteProbe("GET", f"/mcp_servers/{UPSTREAM}/logs", handler_asks="read"),
    RouteProbe("POST", f"/mcp_servers/{UPSTREAM}/start", handler_asks="lifecycle"),
    RouteProbe(
        "POST",
        "/mcp_servers",
        {"mcp_server_id": SCRATCH_SERVER, "mode": "docker", "image": "example/scratch:1"},
        handler_asks="local_launch",
    ),
    RouteProbe("PUT", _SERVER, {"description": "probed"}, handler_asks="write"),
    # An environment change on a local server is a launch.
    RouteProbe("PATCH", _SERVER, {"env": {"LOG_LEVEL": "info"}}, handler_asks="local_launch"),
    RouteProbe("POST", _L7, _ALLOW_ALL),
    RouteProbe("PUT", _L7, _ALLOW_ALL),
    RouteProbe("GET", _L7),
    RouteProbe("DELETE", _L7),
    RouteProbe("POST", f"{_SERVER}/stop", handler_asks="lifecycle"),
    RouteProbe("POST", f"{_SERVER}/block", handler_asks="lifecycle"),
    RouteProbe("DELETE", _SERVER, handler_asks="lifecycle"),
    RouteProbe("DELETE", f"/mcp_servers/{PINNED_SERVER}/peer_identity", handler_asks="peer_identity", first=_PINNED),
    # --- groups
    RouteProbe("POST", "/groups", {"group_id": GROUP}),
    RouteProbe("GET", _GROUP),
    RouteProbe("PUT", _GROUP, {"description": "probed"}),
    RouteProbe("POST", f"{_GROUP}/members", {"member_id": UPSTREAM}),
    RouteProbe("POST", f"{_GROUP}/rebalance"),
    RouteProbe("DELETE", f"{_GROUP}/members/{UPSTREAM}"),
    RouteProbe("DELETE", _GROUP),
    # --- discovery: a source, a scan that finds two definitions, one decision on each.
    RouteProbe(
        "POST",
        "/discovery/sources",
        {"source_type": "filesystem", "mode": "additive", "config": {"path": DISCOVERY_DIR, "watch": False}},
        keeps=("source_id",),
    ),
    RouteProbe("GET", "/discovery/sources"),
    RouteProbe("PUT", _SOURCE, {"mode": "additive"}),
    RouteProbe("PUT", f"{_SOURCE}/enable", {"enabled": True}),
    RouteProbe("POST", f"{_SOURCE}/scan"),
    RouteProbe("GET", "/discovery/pending"),
    RouteProbe("GET", "/discovery/quarantined"),
    RouteProbe("POST", f"/discovery/approve/{TO_APPROVE}"),
    RouteProbe("POST", f"/discovery/reject/{TO_REJECT}"),
    RouteProbe("DELETE", _SOURCE),
    # --- configuration
    RouteProbe("GET", "/config/diff"),
    RouteProbe("POST", "/config/export"),
    RouteProbe("POST", "/config/backup"),
    RouteProbe("POST", "/config/reload"),
    # --- approvals: tenant-aware, so called with the confined grant.
    RouteProbe("GET", _APPROVAL, caller="tenant"),
    RouteProbe("POST", f"{_APPROVAL}/resolve", {"decision": "approve"}, caller="tenant"),
    # --- auth administration
    RouteProbe("POST", "/auth/keys", {"principal_id": TARGET_PRINCIPAL, "name": "probe"}, keeps=("key_id",)),
    RouteProbe("GET", f"/auth/keys?principal_id={TARGET_PRINCIPAL}"),
    RouteProbe("DELETE", "/auth/keys/{key_id}"),
    RouteProbe("GET", "/auth/roles"),
    RouteProbe("GET", "/auth/roles/all"),
    RouteProbe("POST", "/auth/roles", {"role_name": ROLE, "permissions": ["tool:list:*"]}),
    RouteProbe("GET", _ROLE),
    RouteProbe("PATCH", _ROLE, {"permissions": ["tool:list:*"], "description": "probed"}),
    RouteProbe("DELETE", _ROLE),
    RouteProbe("POST", "/auth/roles/assign", _ASSIGNMENT),
    RouteProbe("GET", "/auth/principals"),
    RouteProbe("GET", f"/auth/principals/roles?principal_id={TARGET_PRINCIPAL}"),
    RouteProbe("DELETE", "/auth/roles/revoke", _ASSIGNMENT),
    RouteProbe("GET", "/auth/permissions"),
    RouteProbe("POST", "/auth/check-permission", {"principal_id": TARGET_PRINCIPAL, "permission": "tool:list:*"}),
    RouteProbe("POST", _POLICY, {"allow_list": ["*"]}),
    RouteProbe("GET", _POLICY),
    RouteProbe("DELETE", _POLICY),
)

ROUTE_PROBE_NAMES = tuple(probe.name for probe in ROUTE_PROBES)

#: Routes with no probe, each with the concrete reason one cannot be written.
#: Keyed `METHOD /template`, as `rule_keys` names a rule of the table.
EXEMPT_ROUTES: dict[str, str] = {}
