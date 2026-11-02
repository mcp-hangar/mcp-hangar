"""The `_meta` keys that mark a refusal as Hangar's own (ADR-029 s12.3).

An upstream's failure is relayed as it was sent (#1900), so an upstream could
otherwise write these keys and pass for Hangar, next step included. The keys
mean something to a caller only because no upstream result reaches it with them.
"""

from __future__ import annotations

from typing import Any

#: The classification every shaped refusal carries.
TOOL_FAILURE_KEY = "io.mcp-hangar/toolFailure"
#: The SEP-2643 envelope an authorization denial also carries.
AUTHORIZATION_DENIAL_KEY = "io.mcp-hangar/authorizationDenial"

_REFUSAL_KEYS = frozenset({TOOL_FAILURE_KEY, AUTHORIZATION_DENIAL_KEY})


def without_refusal_keys(result: dict[str, Any]) -> dict[str, Any]:
    """*result* with Hangar's refusal keys gone from its `_meta`; everything else as it was sent."""
    meta = result.get("_meta")
    if not isinstance(meta, dict) or _REFUSAL_KEYS.isdisjoint(meta):
        return result
    kept = {key: value for key, value in meta.items() if key not in _REFUSAL_KEYS}
    return {key: value for key, value in result.items() if key != "_meta"} | ({"_meta": kept} if kept else {})
