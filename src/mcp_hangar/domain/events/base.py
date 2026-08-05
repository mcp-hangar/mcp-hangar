# pyright: reportExplicitAny=false

"""The DomainEvent base and its replay seam."""

from abc import ABC
from dataclasses import dataclass, field
import time
from typing import Any
import uuid


@dataclass(kw_only=True)
class DomainEvent(ABC):
    """Base class for all domain events.

    The identity fields are ``kw_only``, which is what lets this be a dataclass
    at all. Ordinary inherited fields with defaults would force every subclass
    field to have one too ("non-default argument follows default argument"), and
    that constraint is why the base used to be a plain class with an
    ``__init__`` -- at the cost of 99 subclasses each carrying an identical
    three-line ``__post_init__`` whose whole body was ``super().__init__()``.
    Keyword-only fields do not participate in that ordering, so subclasses keep
    their positional signatures unchanged.

    Both fields are ``compare=False``, which preserves the equality semantics
    exactly as they were: when the base was not a dataclass these were not
    fields, so a subclass's generated ``__eq__`` compared the payload alone. Two
    events with the same payload and different ids still compare equal. That is
    arguably the weaker definition -- two distinct occurrences are not the same
    occurrence -- but changing it is a separate decision from removing
    boilerplate, and it would change behaviour silently at every call site that
    compares events.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    occurred_at: float = field(default_factory=time.time, compare=False)

    @classmethod
    def rehydrate(cls, event_id: str | None, occurred_at: float | None, /, **payload: Any) -> "DomainEvent":
        """Rebuild a persisted event, restoring the identity it was stored with.

        Replay must not mint a new ``event_id`` or a new ``occurred_at``: the
        first would break idempotency for any consumer keyed on event id, and
        the second would re-date history to whenever the stream happened to be
        read.

        Now that the identity fields are in the constructor, this passes them
        through rather than assigning after construction. It stays a named
        method because the ``None``-means-keep-the-fresh-one convention is real
        logic that its two call sites -- the event store and the event-sourced
        repository -- would otherwise each reimplement.

        Args:
            event_id: Stored id. ``None`` keeps the freshly minted one.
            occurred_at: Stored timestamp. ``None`` keeps the fresh one.
            **payload: The event's own fields.

        Returns:
            The reconstructed event.
        """
        if event_id is not None:
            payload["event_id"] = event_id
        if occurred_at is not None:
            payload["occurred_at"] = occurred_at
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {"event_type": self.__class__.__name__, **self.__dict__}
