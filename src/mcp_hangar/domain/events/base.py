# pyright: reportExplicitAny=false

"""The DomainEvent base and its replay seam."""

from abc import ABC
import time
from typing import Any
import uuid


class DomainEvent(ABC):
    """
    Base class for all domain events.

    Note: Not a dataclass to avoid inheritance issues.
    Subclasses should be dataclasses.
    """

    def __init__(self):
        self.event_id: str = str(uuid.uuid4())
        self.occurred_at: float = time.time()

    @classmethod
    def rehydrate(cls, event_id: str | None, occurred_at: float | None, /, **payload: Any) -> "DomainEvent":
        """Rebuild a persisted event, restoring the identity it was stored with.

        Replay must not mint a new ``event_id`` or a new ``occurred_at``: the
        first would break idempotency for any consumer keyed on event id, and
        the second would re-date history to whenever the stream happened to be
        read.

        The identity is restored by assignment after construction, because the
        subclasses are dataclasses whose generated ``__init__`` does not accept
        these two fields. That is a wart, and this method exists so it is ONE
        wart with a name rather than the same three lines copied into every
        module that replays a stream -- the event store and the event-sourced
        repository were both reaching into an event's identity directly.

        It is also the seam to change when ``DomainEvent`` becomes a dataclass:
        the identity fields move into the constructor and this body collapses to
        a single call.

        Args:
            event_id: Stored id. ``None`` keeps the freshly minted one.
            occurred_at: Stored timestamp. ``None`` keeps the fresh one.
            **payload: The event's own fields.

        Returns:
            The reconstructed event.
        """
        event = cls(**payload)
        if event_id is not None:
            event.event_id = event_id
        if occurred_at is not None:
            event.occurred_at = occurred_at
        return event

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {"event_type": self.__class__.__name__, **self.__dict__}
