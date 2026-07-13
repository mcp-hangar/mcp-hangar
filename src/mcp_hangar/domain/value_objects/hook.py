"""Hook value objects for SEP-1763 interceptor framework compliance.

Provides the hook abstraction that pairs a domain event with a phase marker,
enabling phase-aware interception of the MCP event pipeline.

- HookPhase: execution phase within the trust-boundary ordering
- Hook: frozen wrapper pairing a DomainEvent with its phase and sequence
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..events import DomainEvent


class HookPhase(StrEnum):
    """Execution phase within the SEP-1763 trust-boundary ordering.

    The internal phases (PRE_VALIDATE .. OBSERVE) mirror the local
    interceptor execution model:
    Validate -> Mutate -> Send (outbound) / Validate -> Mutate -> Process (inbound).

    REQUEST and RESPONSE are the two wire-level phases defined by MCP
    PR #2624 (SEP-1763/SEP-2624), where a Lifecycle Event is either being
    initiated (``request``) or completed (``response``). They are used for
    phase-aware ``interceptor/invoke`` delivery and are kept as the exact
    spec string values so hooks reconcile 1:1 with the pinned wire format.
    """

    PRE_VALIDATE = "pre_validate"
    POST_VALIDATE = "post_validate"
    PRE_MUTATE = "pre_mutate"
    POST_MUTATE = "post_mutate"
    OBSERVE = "observe"
    # Wire-level phases per MCP PR #2624 (see module/interceptors_list pin).
    REQUEST = "request"
    RESPONSE = "response"


@dataclass(frozen=True)
class Hook:
    """Thin wrapper pairing a domain event with a phase marker.

    Hooks provide phase-aware event delivery for interceptor subscribers
    while keeping the existing flat-event path intact for backward compat.

    Attributes:
        event: The underlying domain event.
        phase: Execution phase this hook was emitted at.
        sequence_number: Monotonically increasing counter per correlation_id.
    """

    event: DomainEvent
    phase: HookPhase
    sequence_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.event, DomainEvent):
            raise TypeError("event must be a DomainEvent instance")
        if not isinstance(self.phase, HookPhase):
            raise TypeError("phase must be a HookPhase value")
        if self.sequence_number < 0:
            raise ValueError("sequence_number must be non-negative")
