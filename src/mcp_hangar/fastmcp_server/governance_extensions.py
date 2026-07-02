"""SEP-2133 governance extension advertisement.

Declares the governance mcp-hangar offers as experimental MCP extensions
keyed by reverse-DNS extension IDs. This is a PURE DECLARATION of
*availability*: every extension is off by default and requires explicit
client/operator opt-in (SEP-2133). Advertising an extension does not
enable it.

Honesty (design-claim-discipline): only governance that is actually
enforced today is advertised -- the interceptor validator/mutator framework
(wired, opt-in) and configuration-driven tool-digest pinning. Dormant
capabilities (task governance, ADR-008) are intentionally NOT advertised.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar import __version__

from .interceptors_list import MUTATOR_ID, VALIDATOR_ID

#: Reverse-DNS extension ID for configuration-driven tool-digest pinning.
DIGEST_PINNING_ID = "io.mcp-hangar.digest-pinning"

#: The specification this advertisement conforms to.
_SPEC = "SEP-2133"


def governance_experimental_capabilities() -> dict[str, dict[str, Any]]:
    """Return the ``capabilities.experimental`` governance descriptor map.

    Keyed by reverse-DNS extension ID (SEP-2133). Each descriptor declares
    *availability only*; the ``optIn`` / ``enabledByDefault`` fields mark
    every extension as opt-in and off by default. Advertising here does not
    activate any governance.

    Returns:
        Mapping of reverse-DNS extension ID to its descriptor.
    """
    return {
        VALIDATOR_ID: {
            "spec": _SPEC,
            "version": __version__,
            "type": "validator",
            "description": (
                "Host-side request validator: inspects tools/call and tools/list and audits or blocks fail-closed."
            ),
            "modes": ["audit", "enforce"],
            "optIn": True,
            "enabledByDefault": False,
        },
        MUTATOR_ID: {
            "spec": _SPEC,
            "version": __version__,
            "type": "mutator",
            "description": ("Host-side request mutator: rewrites tools/call arguments before dispatch."),
            "modes": ["enforce"],
            "optIn": True,
            "enabledByDefault": False,
        },
        DIGEST_PINNING_ID: {
            "spec": _SPEC,
            "version": __version__,
            "type": "integrity",
            "description": (
                "Configuration-driven tool-digest pinning: re-verifies a "
                "pinned tool schema digest and audits, warns, or blocks "
                "fail-closed on drift."
            ),
            "modes": ["audit", "warn", "block"],
            "optIn": True,
            "enabledByDefault": False,
        },
    }


__all__ = [
    "DIGEST_PINNING_ID",
    "governance_experimental_capabilities",
]
