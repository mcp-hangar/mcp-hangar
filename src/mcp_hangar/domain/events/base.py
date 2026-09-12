# pyright: reportExplicitAny=false

"""The DomainEvent base and its replay seam."""

from abc import ABC
from dataclasses import dataclass, field, fields
import functools
import time
from typing import Any
import uuid

from ...logging_config import env_length_limit, truncate_text
from .producer import UNKNOWN_PRODUCER, current_instance_id

#: Hangar's bound on a free-text field of a domain event, in characters.
EVENT_TEXT_LENGTH_LIMIT = 4096
EVENT_TEXT_LENGTH_LIMIT_ENV = "MCP_EVENT_TEXT_LENGTH_LIMIT"
#: The field names that hold prose -- an error's message, a reason, a detail --
#: rather than an identifier. Matched by name, so a new event cannot forget.
FREE_TEXT_FIELDS = frozenset(
    {"description", "detail", "error_message", "message", "reason", "reasons", "violation_detail"}
)


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
    #: The instance that produced this event. Defaulted rather than passed at
    #: every construction site: 116 event classes are raised from inside
    #: aggregates, which have no business knowing what a replica is. See
    #: `producer` for why the identity is minted instead of configured.
    produced_by: str = field(default_factory=current_instance_id, compare=False)

    def __post_init__(self) -> None:
        """Bound the free-text fields, once, where the event is made.

        A string in a ``FREE_TEXT_FIELDS`` field, or in a list there, longer than
        MCP_EVENT_TEXT_LENGTH_LIMIT characters (default ``EVENT_TEXT_LENGTH_LIMIT``)
        is cut to it and ends with the truncation marker. The event store,
        ``/ws/events``, the audit trail and the logs are all handed this
        instance, so all of them see the bounded value. A subclass that defines
        its own ``__post_init__`` calls this one.
        """
        names = _free_text_fields(type(self))
        if not names:
            return
        limit = env_length_limit(EVENT_TEXT_LENGTH_LIMIT_ENV) or EVENT_TEXT_LENGTH_LIMIT
        for name in names:
            setattr(self, name, _bounded(getattr(self, name), limit))

    @classmethod
    def rehydrate(
        cls,
        event_id: str | None,
        occurred_at: float | None,
        produced_by: str | None = None,
        /,
        **payload: Any,
    ) -> "DomainEvent":
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

        ``produced_by`` deliberately breaks that convention: absent means
        ``UNKNOWN_PRODUCER``, not "keep the fresh one". Keeping the fresh one
        would have the reading process claim authorship of a row it did not
        write, and a tailer skips what it wrote -- so a row stored before this
        field existed would be silently dropped instead of delivered.

        Args:
            event_id: Stored id. ``None`` keeps the freshly minted one.
            occurred_at: Stored timestamp. ``None`` keeps the fresh one.
            produced_by: Stored producer. ``None`` means the row predates the
                field and reads as ``UNKNOWN_PRODUCER``.
            **payload: The event's own fields.

        Returns:
            The reconstructed event.
        """
        if event_id is not None:
            payload["event_id"] = event_id
        if occurred_at is not None:
            payload["occurred_at"] = occurred_at
        payload["produced_by"] = produced_by if produced_by is not None else UNKNOWN_PRODUCER
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {"event_type": self.__class__.__name__, **self.__dict__}


@functools.cache
def _free_text_fields(cls: type) -> tuple[str, ...]:
    """The fields of event class ``cls`` named in ``FREE_TEXT_FIELDS``."""
    return tuple(f.name for f in fields(cls) if f.name in FREE_TEXT_FIELDS)


def _bounded(value: Any, limit: int) -> Any:
    """``value`` with every string in it cut to ``limit``; the same object when nothing is cut."""
    if isinstance(value, str):
        return truncate_text(value, limit)
    if isinstance(value, list) and any(isinstance(v, str) and len(v) > limit for v in value):
        return [truncate_text(v, limit) if isinstance(v, str) else v for v in value]
    return value
