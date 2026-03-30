"""Value objects for identity propagation."""

from dataclasses import dataclass
from typing import Any, Literal

PrincipalType = Literal["user", "service", "anonymous"]

@dataclass(frozen=True)
class CallerIdentity:
    """Represents the identity of the caller triggering a tool invocation."""

    user_id: str | None
    agent_id: str | None
    session_id: str | None
    principal_type: PrincipalType = "anonymous"

    def __post_init__(self) -> None:
        """Validate identity consistency."""
        if self.principal_type in ("user", "service") and not self.user_id:
            raise ValueError(f"user_id cannot be None when principal_type is '{self.principal_type}'")

@dataclass(frozen=True)
class IdentityContext:
    """Full identity context for a request."""

    caller: CallerIdentity
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for event storage and propagation."""
        return {
            "user_id": self.caller.user_id,
            "agent_id": self.caller.agent_id,
            "session_id": self.caller.session_id,
            "principal_type": self.caller.principal_type,
            "correlation_id": self.correlation_id,
        }

