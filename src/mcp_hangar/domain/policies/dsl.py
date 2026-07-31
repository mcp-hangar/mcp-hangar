"""MCP Policy DSL (v1 proposed grammar) parser + validator.

ADR-006 (Tetragon) ratifies a backend-agnostic *MCP Policy DSL* that compiles
to enforcement backends (Kubernetes ``NetworkPolicy`` today, Cilium/Tetragon
``TracingPolicy`` later) by attaching kprobes to ``tcp_connect``, ``sk_alloc``,
``execve``, and ``openat`` with ``alert``/``block`` enforcement actions.

The DSL *syntax* itself was never specified (its old home was archived). This
module delivers a **v1 proposed grammar** together with a parser/validator that
turns a plain ``dict`` (as loaded from YAML) into a validated, immutable AST
(:class:`PolicyDSL`). It is deliberately backend-agnostic: the compiler that
lowers this AST to concrete enforcement CRDs (e.g. ``TracingPolicy``) lives in
the operator repository and is intentionally OUT OF SCOPE here.

The grammar (v1)::

    name: "<non-empty string>"
    hooks:
      - hook: "tcp_connect" | "sk_alloc" | "execve" | "openat"
        action: "alert" | "block"
        match:                      # optional; an EMPTY match means match-all
          remote_host: "<str>"      # tcp_connect only (non-empty string)
          remote_port: <int 1..65535>  # tcp_connect only
          binary: "<str>"           # execve only (non-empty string)
          path: "<str>"             # openat only (non-empty string)

An empty ``match`` matches every event for the hook; with ``action: block`` that
means "block ALL occurrences of the hook" -- a deliberately broad hammer, so
declare it consciously. v1 validates only value *types/shape* (non-empty string,
port range); filter *semantics* (is this a valid CIDR / hostname / path?) are
deferred to the backend compiler, not checked here.

Parsing is pure and deterministic: the same input ``dict`` always yields an
equal (and hashable) :class:`PolicyDSL`. Validation failures raise
:class:`ValueError` with a message that includes the offending value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_HOOKS",
    "HookRule",
    "PolicyDSL",
    "parse_policy",
]

# Allowed enforcement hooks (kprobe attach points), per ADR-006.
ALLOWED_HOOKS: tuple[str, ...] = ("tcp_connect", "sk_alloc", "execve", "openat")

# Allowed enforcement actions.
ALLOWED_ACTIONS: tuple[str, ...] = ("alert", "block")

# Which ``match`` filter keys are valid for each hook. ``sk_alloc`` takes none.
_MATCH_KEYS_BY_HOOK: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "tcp_connect": ("remote_host", "remote_port"),
        "sk_alloc": (),
        "execve": ("binary",),
        "openat": ("path",),
    }
)

# Allowed top-level policy keys and per-hook keys (unknown keys are rejected).
_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"name", "hooks"})
_HOOK_KEYS: frozenset[str] = frozenset({"hook", "action", "match"})

# Port range for ``remote_port`` (inclusive).
_MIN_PORT = 1
_MAX_PORT = 65535


@dataclass(frozen=True)
class HookRule:
    """A single validated enforcement rule within a policy.

    Immutable AND hashable (usable in ``set``/``dict``): filters are stored as a
    sorted tuple of pairs (``match_pairs``); read them as a mapping via the
    :attr:`match` property.

    Attributes:
        hook: One of :data:`ALLOWED_HOOKS`.
        action: One of :data:`ALLOWED_ACTIONS`.
        match_pairs: Validated filter ``(key, value)`` pairs in sorted key order.
            Empty when the rule has no ``match`` filters (i.e. match-all).
    """

    hook: str
    action: str
    match_pairs: tuple[tuple[str, str | int], ...] = ()

    @property
    def match(self) -> Mapping[str, str | int]:
        """Validated match filters as a read-only mapping (empty if none)."""
        return MappingProxyType(dict(self.match_pairs))


@dataclass(frozen=True)
class PolicyDSL:
    """A validated MCP policy (v1 grammar).

    Attributes:
        name: Non-empty policy name.
        hooks: Non-empty tuple of :class:`HookRule` in source order.
    """

    name: str
    hooks: tuple[HookRule, ...]


def _validate_remote_port(value: Any) -> int:
    """Validate ``remote_port``: an int in ``1..65535``."""
    # Reject bool explicitly: ``bool`` is a subclass of ``int`` in Python.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"remote_port must be an int in 1..65535, got {value!r}")
    port: int = value
    if not (_MIN_PORT <= port <= _MAX_PORT):
        raise ValueError(f"remote_port must be in 1..65535, got {port!r}")
    return port


def _validate_str_filter(key: str, value: Any) -> str:
    """Validate a string-typed filter (``remote_host``/``binary``/``path``)."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string, got {value!r}")
    return value


def _validate_match(hook: str, match: Any) -> tuple[tuple[str, str | int], ...]:
    """Validate a hook's ``match`` filters against the keys allowed for ``hook``.

    Returns the validated filters as a sorted, hashable tuple of ``(key, value)``
    pairs (empty tuple when there are no filters).
    """
    if match is None:
        return ()
    if not isinstance(match, Mapping):
        raise ValueError(f"match must be a mapping, got {match!r}")

    allowed = _MATCH_KEYS_BY_HOOK[hook]
    validated: list[tuple[str, str | int]] = []
    # Iterate in sorted key order for deterministic construction.
    for key in sorted(match):
        if key not in allowed:
            raise ValueError(f"match key {key!r} is not valid for hook {hook!r}; allowed keys: {list(allowed)}")
        value = match[key]
        if key == "remote_port":
            validated.append((key, _validate_remote_port(value)))
        else:
            validated.append((key, _validate_str_filter(key, value)))
    return tuple(validated)


def _validate_hook(index: int, raw: Any) -> HookRule:
    """Validate a single raw hook entry into a :class:`HookRule`."""
    if not isinstance(raw, Mapping):
        raise ValueError(f"hooks[{index}] must be a mapping, got {raw!r}")

    unknown = set(raw) - _HOOK_KEYS
    if unknown:
        raise ValueError(f"hooks[{index}] has unknown keys: {sorted(unknown)}; allowed keys: {sorted(_HOOK_KEYS)}")

    if "hook" not in raw:
        raise ValueError(f"hooks[{index}] is missing required key 'hook'")
    hook = raw["hook"]
    if hook not in ALLOWED_HOOKS:
        raise ValueError(f"hooks[{index}] has invalid hook {hook!r}; allowed: {list(ALLOWED_HOOKS)}")

    if "action" not in raw:
        raise ValueError(f"hooks[{index}] is missing required key 'action'")
    action = raw["action"]
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"hooks[{index}] has invalid action {action!r}; allowed: {list(ALLOWED_ACTIONS)}")

    match_pairs = _validate_match(hook, raw.get("match"))
    return HookRule(hook=hook, action=action, match_pairs=match_pairs)


def parse_policy(data: Any) -> PolicyDSL:
    """Parse and validate a policy ``dict`` into a :class:`PolicyDSL` AST.

    Args:
        data: A mapping as loaded from YAML/JSON.

    Returns:
        A validated, immutable :class:`PolicyDSL`. Deterministic: the same input
        always yields an equal AST.

    Raises:
        ValueError: If the policy violates the v1 grammar. The message includes
            the offending value.
    """
    if not isinstance(data, Mapping):
        raise ValueError(f"policy must be a mapping, got {data!r}")

    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise ValueError(
            f"policy has unknown top-level keys: {sorted(unknown)}; allowed keys: {sorted(_TOP_LEVEL_KEYS)}"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"policy 'name' must be a non-empty string, got {name!r}")

    hooks = data.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        raise ValueError(f"policy 'hooks' must be a non-empty list, got {hooks!r}")

    rules = tuple(_validate_hook(i, raw) for i, raw in enumerate(hooks))
    return PolicyDSL(name=name, hooks=rules)
