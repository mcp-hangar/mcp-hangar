"""Backwards-compatibility helpers for the Provider -> McpServer migration."""

from __future__ import annotations

from collections.abc import Callable
import functools


def resolve_legacy_mcp_server_id(mcp_server_id: str | None, kwargs: dict[str, object]) -> str:
    """Resolve mcp_server_id from kwargs, supporting legacy provider_id alias.

    Mutates kwargs by popping provider_id if present.

    Used by the frozen CQRS command dataclasses, which cannot use
    :func:`accepts_legacy_kwarg`: their fields are set through
    ``object.__setattr__`` rather than by a generated ``__init__``.
    """
    if mcp_server_id is not None:
        return mcp_server_id
    legacy_id = kwargs.pop("provider_id", None)
    if isinstance(legacy_id, str):
        return legacy_id
    raise TypeError("Missing required argument: mcp_server_id")


def accepts_legacy_kwarg(legacy: str, modern: str) -> Callable[[type], type]:
    """Let a constructor keep accepting a pre-rename keyword.

    Two renames left callers spelling arguments the old way: ``provider_id`` ->
    ``mcp_server_id`` and ``provider_name`` -> ``mcp_server_name``. Before this,
    twenty-eight classes each carried a hand-written ``__init__`` whose only job
    was that translation, plus re-assigning every field and re-raising
    ``TypeError`` on unknown keywords -- roughly ten lines apiece that the
    dataclass machinery already does.

    Wrapping the generated ``__init__`` instead keeps one copy of the rule. The
    field assignment, the required-argument check and the unknown-keyword
    ``TypeError`` all come back from the dataclass, so they cannot drift between
    classes -- which they had: three of the aliases had silently stopped
    accepting the modern spelling at all.

    Passing BOTH spellings with conflicting values raises. The hand-written
    constructors disagreed about this -- the ``*_id`` family raised, the
    ``*_name`` family silently took the legacy value -- and of the two, raising
    is the one that cannot lose a caller's data. Identical values are accepted,
    since there is nothing to disambiguate, and a falsy legacy value falls
    through to the modern one as before.
    """

    def decorate(cls: type) -> type:
        original_init = cls.__dict__.get("__init__") or getattr(cls, "__init__")  # noqa: B009 -- see below

        @functools.wraps(original_init)
        def patched_init(self: object, *args: object, **kwargs: object) -> None:
            legacy_value = kwargs.pop(legacy, None)
            if legacy_value:
                modern_value = kwargs.get(modern)
                if modern_value and modern_value != legacy_value:
                    raise TypeError(
                        f"{cls.__name__} got both {legacy}={legacy_value!r} and "
                        f"{modern}={modern_value!r}; {legacy} is the deprecated spelling of "
                        f"{modern}, so pass only one"
                    )
                kwargs[modern] = legacy_value
            original_init(self, *args, **kwargs)

        setattr(cls, "__init__", patched_init)  # noqa: B010 -- mypy rejects `cls.__init__ = ...` on a `type`
        # An explicit marker, because the wrapper is not introspectable: functools.wraps
        # sets __wrapped__, so inspect.signature reports the dataclass constructor and
        # the legacy keyword is invisible. Tooling that needs to know which classes
        # honour an alias reads this mapping rather than guessing from a signature.
        aliases = dict(getattr(cls, "__legacy_kwarg_aliases__", {}))
        aliases[legacy] = modern
        cls.__legacy_kwarg_aliases__ = aliases  # type: ignore[attr-defined]
        return cls

    return decorate


accepts_legacy_provider_id = accepts_legacy_kwarg("provider_id", "mcp_server_id")
"""Accept the pre-rename ``provider_id`` alongside ``mcp_server_id``."""

accepts_legacy_provider_name = accepts_legacy_kwarg("provider_name", "mcp_server_name")
"""Accept the pre-rename ``provider_name`` alongside ``mcp_server_name``."""
