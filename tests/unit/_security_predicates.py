"""The security predicates, and the served request paths each one must run on (#1386).

Twice a check existed, read as if it did its job, and nothing called it.
``is_session_suspended()`` refused no request until 2.19.1
(GHSA-fhwh-fmq2-7m5c), and nothing observed ``PROJECTED_TOOLS`` (#1059). The
dead-symbol ratchet cannot see this class of defect. It counts any reference in
``src/`` or ``tests/`` as a use, so a predicate that only its own unit tests
call, or that runs only on a path nothing serves, passes it.

This table is the one place those predicates are named, the way
``TOOL_PERMISSIONS`` is for tools. Two tests read it:

* ``test_security_predicates_are_reachable.py`` drives the app
  ``mcp-hangar serve --http`` serves -- its MCP endpoint, its REST API and its
  WebSocket event stream -- and, each in a process of its own, the Python
  facade's ``invoke``, a stdio session and the REST routes that change the fleet
  (``_rest_route_probes.py``). It fails when a declared path never runs its
  predicate, and when a route of the REST API has no probe.
* ``test_security_predicates_are_registered.py`` fails when a function in
  ``src/`` has a predicate's name and is neither registered here nor in
  ``EXEMPT``.

To add a predicate, give it an entry naming every served path it must run on.
If one of those paths has no probe yet, add the probe to the reachability test
first.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mcp_hangar
from mcp_hangar.application.read_models.tool_projection import ToolProjection, ToolProjectionRegistry
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.application.validators.payload_size import PayloadSizeValidator
from mcp_hangar.approvals.commands.resolve import ResolveApprovalHandler
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import evaluate_headers
from mcp_hangar.domain.services._policy_merge import is_allowed
from mcp_hangar.domain.services.access_decision import decide
from mcp_hangar.domain.services.ui_resource_guard import UiResourceGuard
from mcp_hangar.fastmcp_server.flat_tool_projection import _authorize_flat_call, is_governed_allowed
from mcp_hangar.infrastructure.session_suspension import InMemorySessionSuspensionRegistry
from mcp_hangar.server.api.mcp_servers import _check_permission
from mcp_hangar.server.api.middleware import AuthorizationEnforcementMiddleware
from mcp_hangar.server.api.route_permissions import resolve_rule
from mcp_hangar.server.api.tenant_scope import confined_tenant
from mcp_hangar.server.api.ws.filters import visible_to_tenant
from mcp_hangar.server.session_guard import refuse_if_session_suspended, refuse_request_if_session_suspended
from mcp_hangar.server.tools.batch import _authorize_calls, _refuse_if_param_headers_unchecked
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.tool_permissions import authorize_tool
from tests.unit._rest_route_probes import ROUTE_PROBE_NAMES, ROUTE_PROBES

SRC = Path(mcp_hangar.__file__).resolve().parent

# --- the served request paths --------------------------------------------------
#
# One per probe in the reachability test. A name says which surface serves it:
# the default `egress` topology, or a `front_door`.

HANGAR_CALL = "hangar_call"
MANAGEMENT_TOOL = "hangar_* tool"
TASKS_GET = "tasks/get"
TASKS_CANCEL = "tasks/cancel"
TASKS_UPDATE = "tasks/update"
FLAT_TOOL_CALL = "front door tools/call"
FRONT_DOOR_MANAGEMENT_TOOL = "front door hangar_* tool"
# On a task the front door's flat tools/call created (#1394). The three
# `tasks/*` paths above act on one `hangar_call` created.
FRONT_DOOR_TASKS_GET = "front door tasks/get"
FRONT_DOOR_TASKS_CANCEL = "front door tasks/cancel"
FRONT_DOOR_TASKS_UPDATE = "front door tasks/update"
PROMPTS_LIST = "front door prompts/list"
PROMPTS_GET = "front door prompts/get"
COMPLETION = "front door completion/complete"
RESOURCES_LIST = "front door resources/list"
RESOURCE_TEMPLATES_LIST = "front door resources/templates/list"
RESOURCES_READ = "front door resources/read"
# The read-only management tools that list a server's tools and describe it (#1786),
# and the SEP-2575 discovery route; each has its own tenant filter.
HANGAR_TOOLS = "hangar_tools"
HANGAR_DETAILS = "hangar_details"
FRONT_DOOR_HANGAR_TOOLS = "front door hangar_tools"
FRONT_DOOR_HANGAR_DETAILS = "front door hangar_details"
SERVER_DISCOVER = "front door server/discover"
# The front door's `subscriptions/listen`, asked for one resource: the stream's
# tenant projection and the resources it may subscribe to (#1027).
SUBSCRIPTIONS_LISTEN = "front door subscriptions/listen"
# The surfaces served without an MCP request over HTTP (#1786). Each needs a
# process of its own: the facade's `invoke` and a stdio session, on egress
# (`hangar_call`) and on the front door (the flat call).
FACADE_INVOKE = "facade invoke"
STDIO_HANGAR_CALL = "stdio hangar_call"
STDIO_FLAT_TOOL_CALL = "stdio front door tools/call"
# The REST API and the WebSocket event stream `serve --http` mounts at `/api`.
# A route marked (tenant) is `tenant_aware` and is called with a grant confined
# to the caller's tenant, so the confinement is what is exercised.
REST_SERVERS = "GET /api/mcp_servers"
REST_SERVER = "GET /api/mcp_servers/{id}"
REST_SERVER_TOOLS = "GET /api/mcp_servers/{id}/tools"
REST_TOOL_HISTORY = "GET /api/mcp_servers/{id}/tools/history (tenant)"
REST_GROUPS = "GET /api/groups"
REST_TOOLS = "GET /api/tools"
REST_SYSTEM_ME = "GET /api/system/me"
REST_CONFIG = "GET /api/config"
REST_SESSION_SUSPEND = "POST /api/sessions/{id}/suspend"
REST_SESSION_LIFT = "DELETE /api/sessions/{id}/suspend"
REST_PINS = "GET /api/admin/tools/pins (tenant)"
REST_WITHDRAW = "POST /api/admin/tools/{server}/{tool}/withdraw (tenant)"
REST_RESTORE = "POST /api/admin/tools/{server}/{tool}/restore (tenant)"
REST_APPROVALS = "GET /api/approvals (tenant)"
WS_EVENTS = "WebSocket /api/ws/events (tenant)"
# The rest of the REST API, driven in a process of its own: `_rest_route_probes.py`
# names each one, and the rows below take them from there. Two are read by name.
REST_APPROVAL = next(name for name in ROUTE_PROBE_NAMES if name.startswith("GET /api/approvals/"))
REST_APPROVAL_RESOLVE = next(name for name in ROUTE_PROBE_NAMES if name.endswith("/resolve (tenant)"))

_TASK_PATHS = (
    TASKS_GET,
    TASKS_CANCEL,
    TASKS_UPDATE,
    FRONT_DOOR_TASKS_GET,
    FRONT_DOOR_TASKS_CANCEL,
    FRONT_DOOR_TASKS_UPDATE,
)
_PROMPT_PATHS = (PROMPTS_LIST, PROMPTS_GET, COMPLETION)
_RESOURCE_PATHS = (RESOURCES_LIST, RESOURCE_TEMPLATES_LIST, RESOURCES_READ)
_INVOKE_PATHS = (HANGAR_CALL, FLAT_TOOL_CALL)
#: The invoke paths, and every other path that runs one tool call through the executor (#1786). The
#: facade's `invoke` has no session and no request headers (`call_as`), so the
#: session guard and the `Mcp-Param-*` check have nothing to read on it. A stdio
#: session carries no session id (ADR-026), so its guard runs and has nothing to
#: look up.
_EXECUTOR_PATHS = _INVOKE_PATHS + (FACADE_INVOKE, STDIO_HANGAR_CALL, STDIO_FLAT_TOOL_CALL)
_IN_PROCESS_REST_PATHS = (
    REST_SERVERS,
    REST_SERVER,
    REST_SERVER_TOOLS,
    REST_TOOL_HISTORY,
    REST_GROUPS,
    REST_TOOLS,
    REST_SYSTEM_ME,
    REST_CONFIG,
    REST_SESSION_SUSPEND,
    REST_SESSION_LIFT,
    REST_PINS,
    REST_WITHDRAW,
    REST_RESTORE,
    REST_APPROVALS,
)
_REST_PATHS = _IN_PROCESS_REST_PATHS + ROUTE_PROBE_NAMES
#: The routes the route guard asks the authorizer about: all but those whose rule
#: names no permission, which describe the caller itself (`AUTHENTICATED_ONLY`).
_REST_AUTHORIZED_PATHS = (
    tuple(p for p in _IN_PROCESS_REST_PATHS if p != REST_SYSTEM_ME)
    + tuple(probe.name for probe in ROUTE_PROBES if probe.rule is not None and probe.rule.permission is not None)
    + (WS_EVENTS,)
)
#: The `tenant_aware` routes, which confine themselves by the guard's recorded decision.
_REST_TENANT_AWARE_PATHS = (
    REST_TOOL_HISTORY,
    REST_PINS,
    REST_WITHDRAW,
    REST_RESTORE,
    REST_APPROVALS,
    REST_APPROVAL,
    WS_EVENTS,
)
#: The read-only management tools that list or describe a server (#1786). They are
#: management tools like any other: session guard, role check, authorizer.
_LISTING_TOOL_PATHS = (HANGAR_TOOLS, HANGAR_DETAILS, FRONT_DOOR_HANGAR_TOOLS, FRONT_DOOR_HANGAR_DETAILS)
#: Where the front door projects a server's tools for the caller (#1786).
_FRONT_DOOR_PROJECTION_PATHS = (FRONT_DOOR_HANGAR_TOOLS, FRONT_DOOR_HANGAR_DETAILS, SERVER_DISCOVER)
_SCOPE_IS_ALLOWED = {"mcp_server_id": "server", "member_server_id": "server", "member_id": "tenant"}


@dataclass(frozen=True)
class Predicate:
    """One check, and the served paths it must run on.

    Attributes:
        function: The predicate. The spy matches on its code object, so it sees
            a call however the caller reached the function: an import alias,
            the executor's tuple of gates, or a closure.
        paths: Each served path the predicate must run on, mapped to the value
            ``by`` must have there. ``None`` means any call counts.
        by: The argument that says which call site ran the predicate, when one
            request reaches it from two. On a front-door prompt request the flat
            tool map calls ``is_governed_allowed`` with ``kind="tool"``, so a
            deleted ``kind="prompt"`` check would go unnoticed without it.
        scope: The parameters that carry the request's scope, each mapped to what
            it must hold: ``tenant`` (the caller's tenant, or None) or ``server``
            (the target server, or None). A predicate reached with the wrong key
            in a slot -- a member server id in the tenant slot -- ran, and decided
            about the wrong thing (#1786). A parameter may name an attribute
            under one (``principal.tenant_id``).
        returns: What the return value must hold, in the same terms, for a
            predicate whose answer is a scope: ``confined_tenant`` says which
            tenant a REST request is confined to.
    """

    function: Callable[..., Any]
    paths: Mapping[str, str | None]
    by: str | None = None
    scope: Mapping[str, str] = field(default_factory=dict)
    returns: str | None = None

    @property
    def name(self) -> str:
        return self.function.__qualname__

    @property
    def location(self) -> str:
        """``<file under src/mcp_hangar>::<qualname>``, the static check's key."""
        source = Path(self.function.__code__.co_filename).resolve()
        return f"{source.relative_to(SRC).as_posix()}::{self.function.__qualname__}"


def _each(paths: tuple[str, ...], value: str | None = None) -> dict[str, str | None]:
    return dict.fromkeys(paths, value)


PREDICATES: tuple[Predicate, ...] = (
    # A suspended session is refused on every path that does work for it
    # (GHSA-fhwh-fmq2-7m5c). `chokepoint` names the call site. The facade's
    # `invoke` has no session to refuse (`call_as`); a stdio session runs the
    # guard, and carries no session id for it to find (ADR-026).
    Predicate(
        refuse_if_session_suspended,
        by="chokepoint",
        paths={
            HANGAR_CALL: "hangar_call",
            STDIO_HANGAR_CALL: "hangar_call",
            MANAGEMENT_TOOL: "management_tool",
            FLAT_TOOL_CALL: "flat_tool",
            STDIO_FLAT_TOOL_CALL: "flat_tool",
            FRONT_DOOR_MANAGEMENT_TOOL: "management_tool",
            **_each(_LISTING_TOOL_PATHS, "management_tool"),
            **_each(_TASK_PATHS, "task_relay"),
            PROMPTS_LIST: "prompt",
            PROMPTS_GET: "prompt",
            COMPLETION: "completion",
            **_each(_RESOURCE_PATHS, "resource"),
            SUBSCRIPTIONS_LISTEN: "subscription",
        },
    ),
    Predicate(
        refuse_request_if_session_suspended,
        by="chokepoint",
        paths={
            **_each(_TASK_PATHS, "task_relay"),
            PROMPTS_LIST: "prompt",
            PROMPTS_GET: "prompt",
            COMPLETION: "completion",
            **_each(_RESOURCE_PATHS, "resource"),
            SUBSCRIPTIONS_LISTEN: "subscription",
        },
    ),
    # The lookup the guard exists to make. A guard that runs and never finds
    # the caller's session id refuses nothing, the same defect by another route.
    Predicate(
        InMemorySessionSuspensionRegistry.is_suspended,
        paths=_each(
            (HANGAR_CALL, MANAGEMENT_TOOL, FLAT_TOOL_CALL, FRONT_DOOR_MANAGEMENT_TOOL)
            + _LISTING_TOOL_PATHS
            + _TASK_PATHS
            + _PROMPT_PATHS
            + _RESOURCE_PATHS
            + (SUBSCRIPTIONS_LISTEN,)
        ),
    ),
    # Role-based authorization: per tool on the management surface, per call on
    # both invoke paths -- `hangar_call` and the front door's flat call (#1622) --
    # and the authorizer all of them ask.
    Predicate(authorize_tool, paths=_each((MANAGEMENT_TOOL, FRONT_DOOR_MANAGEMENT_TOOL) + _LISTING_TOOL_PATHS)),
    # The REST routes and the event stream ask it from the route guard, and a
    # handler on `mcp_servers` and `admin/tools` asks it again.
    Predicate(_authorize_calls, paths=_each(_EXECUTOR_PATHS)),
    Predicate(_authorize_flat_call, paths=_each((FLAT_TOOL_CALL, STDIO_FLAT_TOOL_CALL))),
    Predicate(
        AuthorizationMiddleware.authorize,
        paths=_each(
            _EXECUTOR_PATHS
            + (MANAGEMENT_TOOL, FRONT_DOOR_MANAGEMENT_TOOL)
            + _LISTING_TOOL_PATHS
            + _REST_AUTHORIZED_PATHS
        ),
        scope={"principal.tenant_id": "tenant"},
    ),
    # A pending approval's resolve authorizes in its handler, by the same rule
    # on every transport, and the grant's reach and the caller's tenant are held
    # against the approval's.
    Predicate(
        ResolveApprovalHandler._authorize, paths={REST_APPROVAL_RESOLVE: None}, scope={"principal.tenant_id": "tenant"}
    ),
    Predicate(
        ResolveApprovalHandler._within_grant,
        paths={REST_APPROVAL_RESOLVE: None},
        scope={"grant.tenant_id": "tenant", "approval.tenant_id": "tenant"},
    ),
    Predicate(
        ResolveApprovalHandler._tenant_matches,
        paths={REST_APPROVAL_RESOLVE: None},
        scope={"principal.tenant_id": "tenant", "approval.tenant_id": "tenant"},
    ),
    # The route guard of the REST API and the event stream: the route's rule,
    # denied when it has none, and how far the grant that passed it reaches,
    # which a `tenant_aware` handler confines itself to.
    Predicate(AuthorizationEnforcementMiddleware.__call__, paths=_each(_REST_PATHS + (WS_EVENTS,))),
    Predicate(resolve_rule, paths=_each(_REST_PATHS + (WS_EVENTS,))),
    Predicate(confined_tenant, paths=_each(_REST_TENANT_AWARE_PATHS), returns="tenant"),
    Predicate(
        _check_permission,
        by="action",
        paths={
            **_each(
                (
                    REST_SERVERS,
                    REST_SERVER,
                    REST_SERVER_TOOLS,
                    REST_TOOL_HISTORY,
                    REST_PINS,
                    REST_WITHDRAW,
                    REST_RESTORE,
                )
            ),
            **{probe.name: probe.handler_asks for probe in ROUTE_PROBES if probe.handler_asks},
        },
    ),
    # A tenant grant restores only its own tenant's withdrawal, never one made for every tenant.
    Predicate(ToolProjectionRegistry.is_withdrawn_for_all_tenants, paths={REST_RESTORE: None}),
    # The event stream delivers an event to a confined subscriber only when it
    # names the subscriber's tenant.
    Predicate(visible_to_tenant, paths={WS_EVENTS: None}, scope={"tenant_id": "tenant"}),
    # Tool-access policy: the decision every surface shares, the executor's
    # call-time check included (#1784).
    Predicate(
        is_allowed,
        by="kind",
        paths={
            **_each(_EXECUTOR_PATHS, "tool"),
            **_each(_FRONT_DOOR_PROJECTION_PATHS, "tool"),
            **_each(_PROMPT_PATHS, "prompt"),
            **_each(_RESOURCE_PATHS, "resource"),
            SUBSCRIPTIONS_LISTEN: "resource",
        },
        scope=_SCOPE_IS_ALLOWED,
    ),
    Predicate(
        is_governed_allowed,
        by="kind",
        paths={
            FLAT_TOOL_CALL: "tool",
            STDIO_FLAT_TOOL_CALL: "tool",
            **_each(_FRONT_DOOR_PROJECTION_PATHS, "tool"),
            **_each(_PROMPT_PATHS, "prompt"),
            **_each(_RESOURCE_PATHS, "resource"),
            SUBSCRIPTIONS_LISTEN: "resource",
        },
        scope={"mcp_server": "server", "tenant_id": "tenant"},
    ),
    # Withdrawal: the overlay `decide()` reads through `withdrawal_status`. A
    # prompt or a resource has no projection, so its status is `is_withdrawn`;
    # a tool's comes off its projection (`is_withdrawn_for`, below).
    Predicate(
        ToolProjectionRegistry.is_withdrawn,
        by="kind",
        paths={
            **_each(_PROMPT_PATHS, "prompt"),
            **_each(_RESOURCE_PATHS, "resource"),
            SUBSCRIPTIONS_LISTEN: "resource",
        },
        scope={"mcp_server": "server", "tenant_id": "tenant"},
    ),
    Predicate(
        ToolProjection.is_withdrawn_for,
        paths=_each(_EXECUTOR_PATHS + _FRONT_DOOR_PROJECTION_PATHS + _LISTING_TOOL_PATHS + (SUBSCRIPTIONS_LISTEN,)),
        scope={"tenant_id": "tenant"},
    ),
    # The one decision every surface makes (#1784): the executor's access and
    # withdrawal gates, each front-door listing and fetch through
    # `is_governed_allowed`, and each name `hangar_tools` and `hangar_details`
    # show. Every scope the target names, then every id a withdrawal can hold
    # under, read through the overlay port.
    Predicate(
        decide,
        paths=_each(
            _EXECUTOR_PATHS
            + _FRONT_DOOR_PROJECTION_PATHS
            + _LISTING_TOOL_PATHS
            + _PROMPT_PATHS
            + _RESOURCE_PATHS
            + (SUBSCRIPTIONS_LISTEN,)
        ),
    ),
    Predicate(
        ToolProjectionRegistry.withdrawal_status,
        by="kind",
        paths={
            **_each(_EXECUTOR_PATHS, "tool"),
            **_each(_FRONT_DOOR_PROJECTION_PATHS, "tool"),
            **_each(_LISTING_TOOL_PATHS, "tool"),
            **_each(_PROMPT_PATHS, "prompt"),
            **_each(_RESOURCE_PATHS, "resource"),
            SUBSCRIPTIONS_LISTEN: "resource",
        },
        scope={"tenant_id": "tenant"},
    ),
    # The executor's digest-pin and approval gates. `_enforce_digest_pins`
    # applies every pin a call must match, a member's group pins included,
    # and `_enforce_digest_pin` checks one of them.
    Predicate(BatchExecutor._enforce_digest_pins, paths=_each(_EXECUTOR_PATHS)),
    Predicate(BatchExecutor._enforce_digest_pin, paths=_each(_EXECUTOR_PATHS)),
    Predicate(BatchExecutor._check_approval_gate, paths=_each(_EXECUTOR_PATHS)),
    # The tenant's execution budget (#1445): the token and the check that the
    # caller has a budget, before the approval hold and the cold start, and the
    # slot, after every gate. The probes' tenant has a budget far above what
    # they spend, so both are taken on each invoke path and refuse nothing.
    Predicate(BatchExecutor._gate_tenant_budget, paths=_each(_EXECUTOR_PATHS)),
    Predicate(BatchExecutor._enforce_tenant_budget, paths=_each(_EXECUTOR_PATHS)),
    # The configured interceptors (#1425). The probes configure one
    # `payload_size` validator, with a cap no probe reaches. The validator is
    # what is watched, not `BatchExecutor._check_validators` or the pipeline
    # that calls it: those run on an empty pipeline too, so a path that
    # dispatched through an executor built without the configured validators
    # would still pass. The front door's flat call did exactly that.
    Predicate(PayloadSizeValidator.validate, paths=_each(_EXECUTOR_PATHS)),
    # The L7 egress policy, enforced by the aggregate. Its Mcp-Param-* header
    # selectors are decided by `evaluate_headers`.
    Predicate(McpServer._enforce_l7_policy, paths=_each(_EXECUTOR_PATHS)),
    Predicate(evaluate_headers, paths=_each(_EXECUTOR_PATHS)),
    # `headers.param_validation.required` on `hangar_call` (#1599). The front
    # door decides it inline in its flat call, before the executor.
    Predicate(_refuse_if_param_headers_unchecked, paths=_each((HANGAR_CALL, STDIO_HANGAR_CALL))),
    # The check made wherever the server's client is handed out -- the invoke
    # path and the relay every task, prompt and resource request makes: the
    # server is READY, and no refusing capability mode refuses its catalogue.
    Predicate(
        McpServer._check_serving,
        paths=_each(_EXECUTOR_PATHS + _TASK_PATHS + _PROMPT_PATHS + _RESOURCE_PATHS + (SUBSCRIPTIONS_LISTEN,)),
    ),
    # Task ownership, asked before a relayed task is read or touched, whichever
    # path created the task: `hangar_call` or the front door's flat call (#1394).
    Predicate(GovernedTaskStore.authorize, paths=_each(_TASK_PATHS)),
    # The ui:// guard (SEP-1865): the allowlist decision every resource
    # surface makes, and the consent enforcement a read makes.
    Predicate(UiResourceGuard.evaluate, paths=_each(_RESOURCE_PATHS + (SUBSCRIPTIONS_LISTEN,))),
    Predicate(UiResourceGuard.enforce, paths=_each((RESOURCES_READ,))),
)

# --- the static check -------------------------------------------------------------

#: Names shaped like a check that allows, refuses or authorizes. Tuned against
#: `src/`: each pattern catches at least one real gate, and none catches a plain
#: helper. The scanner's own tests pin names that must and must not match.
PREDICATE_NAME_PATTERNS: tuple[str, ...] = (
    r"^_?is_\w*(allowed|permitted|authori[sz]ed|suspended|denied|blocked|withdrawn)\w*$",
    r"^_?authori[sz]e\w*$",
    r"^_?refuse(_\w+)?_if_\w+$",
    r"^_?check_\w*(permission|access|approval|auth)\w*$",
    r"^_?enforce(_\w+)?$",
)

#: Predicate-shaped functions that are deliberately not in `PREDICATES`, each
#: with the reason. Keyed like `Predicate.location`.
EXEMPT: dict[str, str] = {
    # The rule behind a registered predicate, not a second gate.
    "application/mcp/tooling.py::_authorize_tool_call": (
        "Runs whichever authorizer is installed. The one installed, authorize_tool, is registered."
    ),
    "application/read_models/tool_projection.py::ToolProjectionRegistry._is_config_withdrawn_for": (
        "Half of the withdrawal overlay behind ToolProjectionRegistry.is_withdrawn, which is registered."
    ),
    "application/read_models/tool_projection.py::ToolProjectionRegistry._is_runtime_withdrawn_for": (
        "Half of the withdrawal overlay behind ToolProjectionRegistry.is_withdrawn, which is registered."
    ),
    "application/read_models/tool_projection.py::ToolProjectionRegistry._is_withdrawn_for": (
        "The overlay lookup behind is_withdrawn and resolve(); ToolProjection.is_withdrawn_for is registered."
    ),
    "domain/services/task_ownership.py::TaskOwnershipRegistry.authorize": (
        "The ownership rule GovernedTaskStore.authorize applies. That method is registered."
    ),
    "domain/value_objects/tool_access_policy.py::ToolAccessPolicy.is_tool_allowed": (
        "A policy's pattern match, asked by _policy_merge.is_allowed, which is registered."
    ),
    "domain/value_objects/tool_access_policy.py::_CompositePolicy.is_tool_allowed": (
        "A policy's pattern match, asked by _policy_merge.is_allowed, which is registered."
    ),
    "domain/value_objects/ui_resource.py::UiResourcePolicy.is_allowed": (
        "The ui:// allowlist match, asked by UiResourceGuard.evaluate, which is registered."
    ),
    # Authorizer backends and interfaces behind AuthorizationMiddleware.authorize.
    "auth/infrastructure/rbac_authorizer.py::RBACAuthorizer.authorize": (
        "A backend AuthorizationMiddleware.authorize delegates to. Configuration picks which one runs."
    ),
    "auth/infrastructure/opa_authorizer.py::OPAAuthorizer.authorize": (
        "A backend AuthorizationMiddleware.authorize delegates to. Configuration picks which one runs."
    ),
    "auth/infrastructure/opa_authorizer.py::CombinedAuthorizer.authorize": (
        "A backend AuthorizationMiddleware.authorize delegates to. Configuration picks which one runs."
    ),
    "domain/contracts/authorization.py::IAuthorizer.authorize": "An interface. No body runs.",
    "domain/contracts/authorization.py::NullAuthorizer.authorize": "The auth-off authorizer. It allows by design.",
    "auth/bootstrap.py::NullAuthComponents.__init__.<locals>.NullAuthorizer.authorize": (
        "The auth-off authorizer. It allows by design."
    ),
    "auth/infrastructure/middleware.py::AuthorizationMiddleware.authorizer": (
        "A read-only accessor returning the backend, so hangar_whoami can show its role bindings. It decides nothing."
    ),
    "domain/contracts/session_suspension.py::ISessionSuspensionRegistry.is_suspended": (
        "An interface. Its implementation, InMemorySessionSuspensionRegistry.is_suspended, is registered."
    ),
    # Not on a probed path.
    "auth/api/routes.py::check_permission": "A REST route that answers a permission question. It grants nothing.",
    "server/bootstrap/reachability.py::enforce_subsystem_reachability": (
        "A startup check on configuration, not a request gate."
    ),
    "auth/infrastructure/jwt_authenticator.py::JWTAuthenticator._enforce_token_lifetime": (
        "Bearer-token validation during authentication. The probes authenticate with API keys."
    ),
    "auth/infrastructure/jwt_authenticator.py::JWTAuthenticator._enforce_tenant_audience": (
        "Bearer-token validation during authentication. The probes authenticate with API keys."
    ),
    "auth/stdio_principal.py::authorizer_over_declared_roles": (
        "Builds the RBAC middleware for the declared stdio principal; it decides nothing itself. The "
        "gates that use it -- authorize_tool, the batch check, ResolveApprovalHandler -- are tested where they run."
    ),
}
