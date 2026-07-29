"""Approval resolution as a command, with authorization at the single chokepoint.

Before this, ``resolve_approval`` called ``ApprovalGateService.resolve()`` straight
from the route and never consulted an authorizer. ``approval:resolve`` existed --
defined in ``auth/roles.py``, mapped from its string form, granted to a role --
and was checked nowhere::

    grep -rn "authorize" src/mcp_hangar/approvals/   ->   (no matches)

Any principal holding a valid token could decide any approval given its id.

## Why authorization lives in the handler, not the route

The obvious place is the route, mirroring ``server/api/mcp_servers.py``. It is the
wrong place here. EPIC A-2919 asks for *one* authorized chokepoint, and its WS-6
folds approvals onto the governed-task path, where the decision arrives as
``tasks/update`` rather than as an HTTP request. A route-level guard would have to
be duplicated there -- and a guard that must be remembered twice is the shape that
produced this bug.

So the command carries an already-authenticated :class:`Principal` and the handler
does the check. Transports parse; the handler decides. A second transport gets
authorization by construction rather than by review.

## What this deliberately does not change

Not-found and already-resolved still surface as the route's existing 404/409
bodies, driven by :class:`ResolveOutcome` rather than by new exception types.
Adding those would mean touching the shared exception/status map for every API in
the process, which is a wider blast radius than this fix earns. Only the
authorization and authentication failures raise, because ``AccessDeniedError`` and
``MissingCredentialsError`` already map to 403 and 401.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...application.commands.commands import Command
from ...domain.contracts.command import CommandHandler
from ...domain.exceptions import MissingCredentialsError
from ...domain.value_objects.security import Principal

#: Authorization tuple for resolving an approval. Matches
#: ``PERMISSION_APPROVAL_RESOLVE`` in ``auth/roles.py`` -- the permission that
#: existed unenforced.
RESOURCE_TYPE = "approval"
ACTION = "resolve"


class ResolveOutcome(Enum):
    """Non-exceptional results the transport renders."""

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    ALREADY_TERMINAL = "already_terminal"
    HOLD_RELEASE_FAILED = "hold_release_failed"


@dataclass(frozen=True)
class ResolveApprovalResult:
    outcome: ResolveOutcome
    state: str | None = None


@dataclass(frozen=True)
class ResolveApprovalCommand(Command):
    """Decide a pending approval.

    ``principal`` is the authenticated caller, never a client-supplied value. The
    transport is responsible for producing it from its own authenticated context;
    a transport that cannot must refuse rather than invent one.
    """

    approval_id: str
    approved: bool
    principal: Principal
    reason: str | None = None


class ResolveApprovalHandler(CommandHandler):
    """Authorize, then resolve.

    ``handle`` is async because :class:`ApprovalGateService` is: releasing the
    in-process hold awaits. It is therefore held on the application context and
    awaited directly, the same way ``LoadMcpServerHandler`` is
    (``server/tools/hangar.py``), rather than registered on the synchronous
    command bus.
    """

    def __init__(self, service: Any, auth_components: Any | None = None) -> None:
        self._service = service
        self._auth_components = auth_components

    def _authorize(self, principal: Principal, approval_id: str) -> None:
        """Raise unless *principal* may resolve *approval_id*.

        Mirrors ``server/api/mcp_servers.py:_check_permission``, including its
        hard-won detail: gate on ``auth_components.enabled``, not on the
        middleware merely being present. An auth-disabled build still ships a real
        ``authz_middleware``, so checking only ``is None`` leaves the guard armed
        with nobody able to satisfy it -- no principal is attached, and every call
        fails closed with no credential that could ever get past it (#600).
        """
        components = self._auth_components
        authz = getattr(components, "authz_middleware", None)

        if authz is None or not getattr(components, "enabled", False):
            return

        if principal is None or principal.is_anonymous():
            raise MissingCredentialsError("Authentication required")

        authz.authorize(
            principal=principal,
            action=ACTION,
            resource_type=RESOURCE_TYPE,
            resource_id=approval_id,
        )

    async def handle(self, command: ResolveApprovalCommand) -> ResolveApprovalResult:  # type: ignore[override]
        self._authorize(command.principal, command.approval_id)

        repository = self._service._repository
        existing = await repository.get(command.approval_id)
        if existing is None:
            return ResolveApprovalResult(ResolveOutcome.NOT_FOUND)
        if existing.is_terminal():
            return ResolveApprovalResult(ResolveOutcome.ALREADY_TERMINAL, state=existing.state.value)

        decided_by = str(command.principal.id)
        success = await self._service.resolve(command.approval_id, command.approved, decided_by, command.reason)
        if not success:
            # The decision is already durable at this point: the service writes
            # state before releasing the hold, so a failed release does not undo
            # it. Reported distinctly so the transport does not imply otherwise.
            return ResolveApprovalResult(ResolveOutcome.HOLD_RELEASE_FAILED)

        updated = await repository.get(command.approval_id)
        return ResolveApprovalResult(
            ResolveOutcome.RESOLVED,
            state=updated.state.value if updated else None,
        )


__all__ = [
    "ACTION",
    "RESOURCE_TYPE",
    "ResolveApprovalCommand",
    "ResolveApprovalHandler",
    "ResolveApprovalResult",
    "ResolveOutcome",
]
