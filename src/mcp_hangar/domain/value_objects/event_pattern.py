"""Wildcard event pattern matching for SEP-1763 subscription filtering.

Supports the four ADR-005 pattern shapes plus exact match:
- ``*`` -- matches any event name
- ``tools/*`` -- matches any event starting with ``tools/``
- ``*/request`` -- matches any event ending with ``/request``
- ``*/response`` -- matches any event ending with ``/response``
- ``tools/call`` -- exact match
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventPattern:
    """Compiled wildcard pattern for event name matching.

    Patterns are validated and pre-compiled at construction time.
    The ``raw`` field preserves the original text for audit logs.
    """

    raw: str
    _segments: tuple[str, ...] = ()
    _match_all: bool = False

    def __init__(self, raw: str) -> None:
        if not raw:
            raise ValueError("pattern cannot be empty")

        object.__setattr__(self, "raw", raw)

        if raw == "*":
            object.__setattr__(self, "_match_all", True)
            object.__setattr__(self, "_segments", ("*",))
            return

        segments = tuple(raw.split("/"))
        # Single-segment patterns (no "/") are valid exact-match patterns.
        # Wildcards are only allowed in multi-segment patterns.

        for seg in segments:
            if not seg:
                raise ValueError(f"pattern has empty segment: {raw!r}")
            if "*" in seg and seg != "*":
                raise ValueError(f"wildcard '*' must be an entire segment, not partial: {raw!r}")

        object.__setattr__(self, "_segments", segments)
        object.__setattr__(self, "_match_all", False)

    def matches(self, event_name: str) -> bool:
        """Test whether an event name matches this pattern."""
        if self._match_all:
            return True

        parts = event_name.split("/")
        if len(parts) != len(self._segments):
            return False

        return all(seg == "*" or seg == part for seg, part in zip(self._segments, parts, strict=True))

    @classmethod
    def parse(cls, value: str) -> EventPattern:
        """Parse a string into an EventPattern. Alias for constructor."""
        return cls(value)

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"EventPattern({self.raw!r})"
