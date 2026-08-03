"""Backwards-compatibility helpers for the Provider -> McpServer migration."""

from __future__ import annotations

import functools


def resolve_legacy_mcp_server_id(mcp_server_id: str | None, kwargs: dict[str, object]) -> str:
    """Resolve mcp_server_id from kwargs, supporting legacy provider_id alias.

    Mutates kwargs by popping provider_id if present.
    """
    if mcp_server_id is not None:
        return mcp_server_id
    legacy_id = kwargs.pop("provider_id", None)
    if isinstance(legacy_id, str):
        return legacy_id
    raise TypeError("Missing required argument: mcp_server_id")


def accepts_legacy_provider_id(cls: type) -> type:
    """Let an event constructor keep accepting the pre-rename ``provider_id``.

    Ten ``Provider*`` alias classes and thirteen renamed events each carried a
    hand-written ``__init__`` whose only job was this translation, plus
    re-assigning every field and re-raising ``TypeError`` on unknown keywords --
    roughly ten lines apiece that the dataclass machinery already does.

    Wrapping the generated ``__init__`` instead keeps one copy of the rule. The
    field assignment, the required-argument check and the unknown-keyword
    ``TypeError`` all come back from the dataclass, so they cannot drift between
    classes -- which they had: three of the aliases silently stopped accepting
    the modern ``mcp_server_id`` spelling at all.

    ``provider_id`` wins when both are given, matching the ``provider_id or
    mcp_server_id`` precedence the hand-written constructors used. A falsy
    ``provider_id`` falls through to ``mcp_server_id``, also as before.
    """
    original_init = cls.__dict__.get("__init__") or getattr(cls, "__init__")  # noqa: B009 -- see below

    @functools.wraps(original_init)
    def patched_init(self: object, *args: object, **kwargs: object) -> None:
        legacy = kwargs.pop("provider_id", None)
        if legacy:
            kwargs["mcp_server_id"] = legacy
        original_init(self, *args, **kwargs)

    setattr(cls, "__init__", patched_init)  # noqa: B010 -- mypy rejects `cls.__init__ = ...` on a `type`
    # An explicit marker, because the wrapper is not introspectable: functools.wraps
    # sets __wrapped__, so inspect.signature reports the dataclass constructor and
    # the legacy keyword is invisible. Tooling that needs to know which classes
    # honour the alias checks this attribute rather than guessing from a signature.
    cls.__accepts_legacy_provider_id__ = True  # type: ignore[attr-defined]
    return cls
