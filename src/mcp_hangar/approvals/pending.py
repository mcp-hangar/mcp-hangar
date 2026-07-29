"""The typed shape of a pending approval, under our own method namespace.

Nothing here goes on the wire yet. This is the model shaped the way we are
asking the protocol to be shaped, built before the asking (A-2919 WS-5,
modelcontextprotocol#2919). The order is deliberate: rebuilding our own
mechanism into the shape we want is a stronger argument than requesting a
feature we do not use.

## Why a namespace of our own

SEP-2663's ``inputRequests`` is a closed union of three methods, two of which
are ``@deprecated`` in the same 2026-07-28 revision. What remains is
``elicitation/create``, and it cannot express *someone other than the caller
must approve this*: it asks the connected client for input, and the connected
client is precisely the party an approval gate exists to not trust.

So the request carries ``method: "io.mcp-hangar/approval"``. Reverse-DNS under a
domain we control, the same convention the protocol uses for extension keys
(``io.modelcontextprotocol/tasks``). If #2919 lands with a different identifier
we migrate an internal constant; if it lands with this one, the value we already
produce fits without translation.

## The one thing this deliberately does NOT do

It does not carry ``requestedSchema``.

Adding one would make the value look like an elicitation to a client that does
not know our method -- and an elicitation is answerable by the caller. That is
exactly the confusion the approval gate exists to prevent: the party making the
call is not the party permitted to approve it. A client that cannot recognise
``io.mcp-hangar/approval`` should be able to *display* that something is
pending, and should have nothing to fill in.

``message`` is therefore present (a human-readable line, for display) and no
schema is. A caller who answers anyway is answering a key that carries no
schema, and the resolution path authorizes ``approval:resolve`` regardless --
see ADR-016. Belt and braces, in that order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Reverse-DNS method identifier for an approval input request, under a domain
#: we control. Candidate contribution to modelcontextprotocol#2919.
APPROVAL_INPUT_METHOD = "io.mcp-hangar/approval"


@dataclass(frozen=True)
class ApprovalSubject:
    """What the decision is about.

    ``arguments_hash`` rather than the arguments: the subject travels to whoever
    is deciding, and the arguments may carry things they should not see. The
    hash still binds the decision to one exact invocation -- approving does not
    approve a *different* call to the same tool.
    """

    mcp_server_id: str
    tool_name: str
    arguments_hash: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "mcpServer": self.mcp_server_id,
            "tool": self.tool_name,
            "argumentsHash": self.arguments_hash,
        }


@dataclass(frozen=True)
class ApprovalPolicyBasis:
    """Why this call needed approving.

    Recorded because "why was I asked?" is the first question an approver has,
    and reconstructing it after the fact from policy state is guesswork. Empty
    is legitimate -- an approval can be requested without a rule naming it.
    """

    rule: str | None = None
    reason: str | None = None

    def to_wire(self) -> dict[str, Any]:
        wire = {}
        if self.rule is not None:
            wire["rule"] = self.rule
        if self.reason is not None:
            wire["reason"] = self.reason
        return wire


@dataclass(frozen=True)
class PendingApproval:
    """A decision waiting on a human, typed.

    Serializes to something insertable as an ``inputRequests`` value with no
    transformation -- that is the WS-5 acceptance, and the reason the field
    names below are camelCase: they are wire names, not Python ones.
    """

    approval_id: str
    subject: ApprovalSubject
    expires_at: datetime
    #: Who may decide. Deliberately a role/permission rather than a person: the
    #: gate authorizes on `approval:resolve`, and naming an individual here
    #: would imply a routing guarantee this model does not make.
    required_permission: str = "approval:resolve"
    policy_basis: ApprovalPolicyBasis = field(default_factory=ApprovalPolicyBasis)
    message: str | None = None

    def _default_message(self) -> str:
        return f"Approval required: {self.subject.tool_name} on {self.subject.mcp_server_id}"

    def to_input_request(self) -> dict[str, Any]:
        """Render as one value of an ``inputRequests`` map.

        Method-discriminated: ``method`` plus ``params``, which is the shape a
        method-carrying union would take. No ``requestedSchema`` -- see the
        module docstring for why that absence is the point rather than an
        omission.
        """
        params: dict[str, Any] = {
            "approvalId": self.approval_id,
            "subject": self.subject.to_wire(),
            "expiresAt": self.expires_at.isoformat(),
            "requiredPermission": self.required_permission,
        }
        basis = self.policy_basis.to_wire()
        if basis:
            params["policyBasis"] = basis

        return {
            "method": APPROVAL_INPUT_METHOD,
            "message": self.message or self._default_message(),
            "params": params,
        }

    @classmethod
    def from_request(cls, request: Any, *, policy_basis: ApprovalPolicyBasis | None = None) -> PendingApproval:
        """Build from the existing :class:`ApprovalRequest` aggregate.

        Takes the hash the aggregate already computed rather than recomputing:
        two hashes of the same arguments that disagree would be worse than none.
        """
        return cls(
            approval_id=request.approval_id,
            subject=ApprovalSubject(
                mcp_server_id=request.provider_id,
                tool_name=request.tool_name,
                arguments_hash=request.arguments_hash,
            ),
            expires_at=request.expires_at,
            policy_basis=policy_basis or ApprovalPolicyBasis(),
        )


__all__ = [
    "APPROVAL_INPUT_METHOD",
    "ApprovalPolicyBasis",
    "ApprovalSubject",
    "PendingApproval",
]
