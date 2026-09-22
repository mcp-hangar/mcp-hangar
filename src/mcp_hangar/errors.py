"""Retryability heuristics for generic exceptions, and the bounded error type.

This module used to hold the ``HangarError`` / ``Rich*`` exception zoo, its
factory functions and ``ErrorClassifier`` -- ~1200 lines nothing in ``src/``
ever raised or constructed (#970, part of the #969 sweep after the factory
cut). The one live caller was ``retry.py`` importing :func:`is_retryable`, so
that function is what remains; the isinstance branches over the deleted types
went with the types. The live exception hierarchy is
``mcp_hangar.domain.exceptions``.

:func:`bounded_error_type` is the one allowlist for what telemetry may say in
place of an error's message (GHSA-qwq2-7g49-jxc6). It lives here, in the shared
kernel and free of dependencies, so the domain, the tracing module and the
integrations all use the same rule without the domain importing a tracing or
OpenTelemetry module (ADR-027).
"""

import re

RETRYABLE_PATTERNS = (
    "timeout",
    "timed out",
    "connection",
    "json",
    "malformed",
    "temporary",
    "transient",
    "retry",
    "network",
)

#: OpenTelemetry's value for an error type that cannot be recorded as it is.
OTHER_ERROR_TYPE = "_OTHER"
#: What an error type may be: a class name, a code, a short token -- never a sentence.
_ERROR_TYPE_SHAPE = re.compile(r"[A-Za-z0-9_.<>\-]{1,128}")


def is_retryable(error: Exception) -> bool:
    """Whether an error looks transient enough to retry.

    Matches common substrings on the exception's message and type name;
    callers with a configured retry policy also match their own
    ``retry_on`` list (see ``retry.should_retry``).
    """
    exc_str = str(error).lower()
    exc_type = type(error).__name__.lower()

    return any(pattern in exc_str or pattern in exc_type for pattern in RETRYABLE_PATTERNS)


class ExpectedRefusal(Exception):
    """Marker: raising this is a decision the gateway made, not a failure it had.

    An egress policy denial, a routing to approval and a spent rate-limit budget
    are the gateway answering the question it exists to answer. They travel as
    exceptions because that is how a refusal stops a call, and `_TextFreeTracer`
    used to mark every span an exception escaped as ERROR -- so an operator
    counting error traces counted refusals, and `hangar.gate.outcome=deny` sat on
    a span whose status said failure (ADR-029 s5).

    A marker class rather than a predicate over error types: the tracer is in the
    shared kernel and cannot import `domain.exceptions`, and an allowlist of names
    somewhere else is a second place to update when an exception is added. What
    the refusal was is still said by `error.type`; only the status differs.

    It derives from `Exception` so it can be caught (`except ExpectedRefusal`) and
    not only tested with `isinstance`, but it is never raised on its own: it is
    mixed into the exceptions the domain already raises, which keep their own
    base and their own message.

    Operational failures are NOT refusals, including the ones whose fail-closed
    verdict is a denial: an approval gate that could not be reached broke, and a
    span that says ERROR is telling the truth about it.
    """


def bounded_error_type(value: object) -> str:
    """``value`` when it is shaped like a class name or code, else ``_OTHER``.

    Letters, digits and ``_.-<>``, at most 128 characters. This is what a span's
    ``error.type`` and ``exception.type`` may hold, and what any other telemetry
    sink -- a log line, an integration -- sends in place of an error's message,
    which can carry what an upstream returned (GHSA-qwq2-7g49-jxc6).
    """
    return value if isinstance(value, str) and _ERROR_TYPE_SHAPE.fullmatch(value) else OTHER_ERROR_TYPE
