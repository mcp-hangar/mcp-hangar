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
  ``mcp-hangar serve --http`` serves. It fails when a declared path never runs
  its predicate.
* ``test_security_predicates_are_registered.py`` fails when a function in
  ``src/`` has a predicate's name and is neither registered here nor in
  ``EXEMPT``.

To add a predicate, give it an entry naming every served path it must run on.
If one of those paths has no probe yet, add the probe to the reachability test
first.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp_hangar
from mcp_hangar.application.read_models.tool_projection import ToolProjection, ToolProjectionRegistry
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.application.validators.payload_size import PayloadSizeValidator
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import evaluate_headers
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.services.ui_resource_guard import UiResourceGuard
from mcp_hangar.fastmcp_server.flat_tool_projection import is_governed_allowed
from mcp_hangar.infrastructure.session_suspension import InMemorySessionSuspensionRegistry
from mcp_hangar.server.session_guard import refuse_if_session_suspended, refuse_request_if_session_suspended
from mcp_hangar.server.tools.batch import _authorize_calls
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.tool_permissions import authorize_tool

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
    """

    function: Callable[..., Any]
    paths: Mapping[str, str | None]
    by: str | None = None

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
    # (GHSA-fhwh-fmq2-7m5c). `chokepoint` names the call site.
    Predicate(
        refuse_if_session_suspended,
        by="chokepoint",
        paths={
            HANGAR_CALL: "hangar_call",
            MANAGEMENT_TOOL: "management_tool",
            FLAT_TOOL_CALL: "flat_tool",
            FRONT_DOOR_MANAGEMENT_TOOL: "management_tool",
            **_each(_TASK_PATHS, "task_relay"),
            PROMPTS_LIST: "prompt",
            PROMPTS_GET: "prompt",
            COMPLETION: "completion",
            **_each(_RESOURCE_PATHS, "resource"),
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
        },
    ),
    # The lookup the guard exists to make. A guard that runs and never finds
    # the caller's session id refuses nothing, the same defect by another route.
    Predicate(
        InMemorySessionSuspensionRegistry.is_suspended,
        paths=_each(
            (HANGAR_CALL, MANAGEMENT_TOOL, FLAT_TOOL_CALL, FRONT_DOOR_MANAGEMENT_TOOL)
            + _TASK_PATHS
            + _PROMPT_PATHS
            + _RESOURCE_PATHS
        ),
    ),
    # Role-based authorization: per tool on the management surface, per call on
    # `hangar_call`, and the authorizer both of them ask.
    Predicate(authorize_tool, paths=_each((MANAGEMENT_TOOL, FRONT_DOOR_MANAGEMENT_TOOL))),
    Predicate(_authorize_calls, paths=_each((HANGAR_CALL,))),
    Predicate(
        AuthorizationMiddleware.authorize,
        paths=_each((HANGAR_CALL, MANAGEMENT_TOOL, FRONT_DOOR_MANAGEMENT_TOOL)),
    ),
    # Tool-access policy: the executor's call-time check, and the decision
    # every projected surface shares.
    Predicate(ToolAccessResolver.is_tool_allowed, paths=_each(_INVOKE_PATHS)),
    Predicate(
        ToolAccessResolver.is_allowed,
        by="kind",
        paths={**_each(_INVOKE_PATHS, "tool"), **_each(_PROMPT_PATHS, "prompt"), **_each(_RESOURCE_PATHS, "resource")},
    ),
    Predicate(
        is_governed_allowed,
        by="kind",
        paths={FLAT_TOOL_CALL: "tool", **_each(_PROMPT_PATHS, "prompt"), **_each(_RESOURCE_PATHS, "resource")},
    ),
    # Withdrawal: the overlay the governed surfaces read, and the check the
    # executor's withdrawal gate makes.
    Predicate(
        ToolProjectionRegistry.is_withdrawn,
        by="kind",
        paths={FLAT_TOOL_CALL: "tool", **_each(_PROMPT_PATHS, "prompt"), **_each(_RESOURCE_PATHS, "resource")},
    ),
    Predicate(ToolProjection.is_withdrawn_for, paths=_each(_INVOKE_PATHS)),
    # The executor's digest-pin and approval gates.
    Predicate(BatchExecutor._enforce_digest_pin, paths=_each(_INVOKE_PATHS)),
    Predicate(BatchExecutor._check_approval_gate, paths=_each(_INVOKE_PATHS)),
    # The configured interceptors (#1425). The probes configure one
    # `payload_size` validator, with a cap no probe reaches. The validator is
    # what is watched, not `BatchExecutor._check_validators` or the pipeline
    # that calls it: those run on an empty pipeline too, so a path that
    # dispatched through an executor built without the configured validators
    # would still pass. The front door's flat call did exactly that.
    Predicate(PayloadSizeValidator.validate, paths=_each(_INVOKE_PATHS)),
    # The L7 egress policy, enforced by the aggregate. Its Mcp-Param-* header
    # selectors are decided by `evaluate_headers`.
    Predicate(McpServer._enforce_l7_policy, paths=_each(_INVOKE_PATHS)),
    Predicate(evaluate_headers, paths=_each(_INVOKE_PATHS)),
    # The check made wherever the server's client is handed out -- the invoke
    # path and the relay every task, prompt and resource request makes: the
    # server is READY, and no refusing capability mode refuses its catalogue.
    Predicate(
        McpServer._check_serving,
        paths=_each(_INVOKE_PATHS + _TASK_PATHS + _PROMPT_PATHS + _RESOURCE_PATHS),
    ),
    # Task ownership, asked before a relayed task is read or touched, whichever
    # path created the task: `hangar_call` or the front door's flat call (#1394).
    Predicate(GovernedTaskStore.authorize, paths=_each(_TASK_PATHS)),
    # The ui:// guard (SEP-1865): the allowlist decision every resource
    # surface makes, and the consent enforcement a read makes.
    Predicate(UiResourceGuard.evaluate, paths=_each(_RESOURCE_PATHS)),
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
        "A policy's pattern match, asked by ToolAccessResolver.is_allowed, which is registered."
    ),
    "domain/value_objects/tool_access_policy.py::_CompositePolicy.is_tool_allowed": (
        "A policy's pattern match, asked by ToolAccessResolver.is_allowed, which is registered."
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
    "domain/contracts/session_suspension.py::ISessionSuspensionRegistry.is_suspended": (
        "An interface. Its implementation, InMemorySessionSuspensionRegistry.is_suspended, is registered."
    ),
    # Not on an MCP request path.
    "server/api/mcp_servers.py::_check_permission": "Guards REST routes, not an MCP request path.",
    "application/read_models/tool_projection.py::ToolProjectionRegistry.is_withdrawn_for_all_tenants": (
        "Scopes a REST restore in admin_tools.py, not an MCP request path."
    ),
    "approvals/commands/resolve.py::ResolveApprovalHandler._authorize": (
        "Authorizes resolving an approval over REST, not an MCP request path."
    ),
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
}
