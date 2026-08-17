"""Retryability heuristics for generic exceptions.

This module used to hold the ``HangarError`` / ``Rich*`` exception zoo, its
factory functions and ``ErrorClassifier`` -- ~1200 lines nothing in ``src/``
ever raised or constructed (#970, part of the #969 sweep after the factory
cut). The one live caller was ``retry.py`` importing :func:`is_retryable`, so
that function is what remains; the isinstance branches over the deleted types
went with the types. The live exception hierarchy is
``mcp_hangar.domain.exceptions``.
"""

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


def is_retryable(error: Exception) -> bool:
    """Whether an error looks transient enough to retry.

    Matches common substrings on the exception's message and type name;
    callers with a configured retry policy also match their own
    ``retry_on`` list (see ``retry.should_retry``).
    """
    exc_str = str(error).lower()
    exc_type = type(error).__name__.lower()

    return any(pattern in exc_str or pattern in exc_type for pattern in RETRYABLE_PATTERNS)
