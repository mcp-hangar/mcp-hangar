"""Value objects for identity propagation."""

from dataclasses import dataclass, replace
from typing import Any, Literal

PrincipalType = Literal["user", "service", "anonymous"]


@dataclass(frozen=True)
class CallerIdentity:
    """Represents the identity of the caller triggering a tool invocation.

    ``roles`` names what authorized one call (#1347): the role the
    authorization decision matched, or ``opa_policy`` for a call an OPA policy
    admitted without one. It is set on a copy of the identity for that call
    only, never at authentication, and stays empty when auth is off or nothing
    decided -- a role is never looked up a second time or invented.
    """

    user_id: str | None
    agent_id: str | None
    session_id: str | None
    principal_type: PrincipalType = "anonymous"
    tenant_id: str | None = None
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate identity consistency."""
        if self.principal_type in ("user", "service") and not self.user_id:
            raise ValueError(f"user_id cannot be None when principal_type is '{self.principal_type}'")


@dataclass(frozen=True)
class IdentityContext:
    """Full identity context for a request."""

    caller: CallerIdentity
    correlation_id: str | None = None

    def with_roles(self, roles: tuple[str, ...]) -> "IdentityContext":
        """A copy whose caller carries *roles* (#1347). This context is left as it is."""
        return replace(self, caller=replace(self.caller, roles=tuple(roles)))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for event storage and propagation."""
        return {
            "user_id": self.caller.user_id,
            "agent_id": self.caller.agent_id,
            "session_id": self.caller.session_id,
            "principal_type": self.caller.principal_type,
            "tenant_id": self.caller.tenant_id,
            "roles": list(self.caller.roles),
            "correlation_id": self.correlation_id,
        }
