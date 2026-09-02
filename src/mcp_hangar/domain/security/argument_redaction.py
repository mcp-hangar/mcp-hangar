"""Redacting tool-call arguments on every path that keeps or shows them.

Arguments are the payload of a governed call, and they routinely carry the
credential the call is made with. This is the one place that decides what a
persisted or served copy of them looks like.

It lives here, rather than in `approvals` where it was written, because
approvals were never the only exit. The same dict reached the event store and
`/ws/events` with no redaction at all, so a secret sat in SQLite or Postgres for
the retention of the log and was served to every `audit:read` holder -- while
the approval record next to it, built from the same arguments, was two-pass
redacted and the log pipeline printed `[REDACTED]` (#1168).

Confidentiality and integrity are separate jobs and both live here:
:func:`redact_arguments` produces the copy that is safe to keep, and
:func:`hash_arguments` produces the identity of the RAW payload, which is what
lets an audit trail correlate a call without holding its values.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ...redactor import get_default_redactor

#: Key-name substrings whose value is never shown, wherever the key appears.
_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "key", "auth", "credential")

#: How deep the walk goes before it stops trying. One deeper than the log
#: pipeline's cap, because the top-level mapping counts as a level here.
_MAX_SCRUB_DEPTH = 6

#: What replaces a value the walk will not descend into. A marker rather than
#: the value, because this projection is persisted and served: dropping what it
#: cannot inspect is the fail-closed direction, and the log pipeline's choice to
#: pass it through does not transfer to a record under an ``approval:read``
#: grant.
_TOO_DEEP = "[REDACTED:depth-limit]"


def is_sensitive_key(key: Any) -> bool:
    """Whether a mapping key names something whose value must never be shown."""
    lowered = str(key).lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from arguments before they are persisted or delivered.

    Two passes, because either alone leaks:

    1. by key name -- ``password``, ``token``, ``secret``, ``key``, ``auth``,
       ``credential`` as substrings;
    2. by value shape, using the shared builtin-pattern redactor (JWTs, Bearer
       headers, ``ghp_``/``AKIA``/``xox``-style keys, URLs carrying credentials).

    Pass 1 alone was the whole of this function, so a secret under a
    non-matching key -- ``{"body": "Authorization: Bearer eyJ..."}`` or
    ``{"dsn": "postgres://user:pw@host"}`` -- was written verbatim into the
    SQLite approval record and served to every ``approval:read`` holder through
    the REST DTO. The value redactor already existed and is used by the log
    pipeline and the stderr capture; approvals just were not using it.

    **Pass 1 runs at every level** (#1130). It used to run only over the
    top-level mapping, so the inverse of the leak above was open: a plain
    password one level down -- ``{"config": {"password": "hunter2"}}``, or the
    same inside a list of records -- has no shape for pass 2 to recognise and
    was stored and served verbatim. Nested arguments are ordinary MCP shapes,
    not an exotic case.

    Nested dicts and lists are walked, since MCP tool arguments are arbitrary
    JSON. Past :data:`_MAX_SCRUB_DEPTH` the subtree is replaced rather than
    passed through.
    """
    redactor = get_default_redactor()

    def scrub(value: Any, depth: int) -> Any:
        if depth > _MAX_SCRUB_DEPTH:
            return _TOO_DEEP
        if isinstance(value, str):
            return redactor.redact(value)
        if isinstance(value, dict):
            return {k: ("[REDACTED]" if is_sensitive_key(k) else scrub(v, depth + 1)) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(item, depth + 1) for item in value]
        return value

    return {key: ("[REDACTED]" if is_sensitive_key(key) else scrub(value, 1)) for key, value in arguments.items()}


def hash_arguments(arguments: dict[str, Any]) -> str:
    """SHA-256 over the RAW arguments, for the dispatch-time integrity check.

    Confidentiality and integrity are different jobs and this hash is the
    integrity one: it answers "is the payload about to be dispatched the payload
    the approver saw approved". Hashing the *redacted* copy instead -- which is
    what this did while redaction was key-name-only, and which would become
    actively unsafe now that values are redacted too -- makes the check blind to
    exactly the substitutions worth catching: two different tokens both redact
    to the same marker, hash identically, and swap freely between approval and
    dispatch.

    Upgrade note: approvals already pending when this ships were hashed over the
    old (sanitized) projection, so they will fail revalidation and be refused.
    That is the fail-closed direction -- a refused approval can be re-requested;
    a silently accepted substitution cannot be undone.
    """
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
