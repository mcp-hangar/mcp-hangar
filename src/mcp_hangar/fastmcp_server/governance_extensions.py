"""SEP-2133 governance extension advertisement.

Declares the governance mcp-hangar offers as MCP extensions keyed by SEP-2133
identifiers (``io.mcp-hangar/<name>``): under ``experimental`` on the handshake
era, under ``extensions`` on the 2026-07-28 surface. This is a PURE DECLARATION of
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

#: Extension ID (SEP-2133 form ``{vendor-prefix}/{extension-name}``) for
#: configuration-driven tool-digest pinning.
DIGEST_PINNING_ID = "io.mcp-hangar/digest-pinning"

#: The identifiers before #1898 spelled the separator as a dot. They stay in
#: ``capabilities.experimental`` of the handshake era for one minor, so a client
#: that looked them up keeps finding them; the modern surface never carries them.
LEGACY_DOTTED_IDS = {
    "io.mcp-hangar.validator": VALIDATOR_ID,
    "io.mcp-hangar.mutator": MUTATOR_ID,
    "io.mcp-hangar.digest-pinning": DIGEST_PINNING_ID,
}

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
    current = {
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
    aliases = {old: {**current[new], "deprecated": True, "replacedBy": new} for old, new in LEGACY_DOTTED_IDS.items()}
    return {**current, **aliases}


def move_governance_to_extensions(capabilities: dict[str, Any]) -> dict[str, Any]:
    """Re-home the governance advertisement for the 2026-07-28 surface.

    That generation has a dedicated ``capabilities.extensions`` field and no
    handshake, so the governance descriptors leave ``experimental`` for it, and
    the deprecated dotted aliases are dropped: nobody looked them up there.

    Args:
        capabilities: A dumped ``ServerCapabilities`` (wire names), as the
            handshake era would serve it.

    Returns:
        A copy with the descriptors under ``extensions``.
    """
    experimental = dict(capabilities.get("experimental") or {})
    current_ids = set(LEGACY_DOTTED_IDS.values())
    moved = {key: value for key, value in experimental.items() if key in current_ids}
    if not moved:
        return capabilities
    out = dict(capabilities)
    for key in (*current_ids, *LEGACY_DOTTED_IDS):
        experimental.pop(key, None)
    if experimental:
        out["experimental"] = experimental
    else:
        out.pop("experimental", None)
    out["extensions"] = {**(capabilities.get("extensions") or {}), **moved}
    return out


__all__ = [
    "DIGEST_PINNING_ID",
    "LEGACY_DOTTED_IDS",
    "advertise_governance_extensions",
    "governance_experimental_capabilities",
    "move_governance_to_extensions",
]


def advertise_governance_extensions(mcp: Any) -> None:
    """Advertise Hangar governance as SEP-2133 experimental extensions on *mcp*.

    A PURE DECLARATION of availability: no behavior changes, every advertised
    extension is off by default, and only governance that is actually enforced
    today is listed (see :func:`governance_experimental_capabilities`).

    Wraps ``get_capabilities`` rather than ``create_initialization_options``.
    Both are read by the handshake, but only the former is read by the stateless
    ``server/discover`` surface — and a 2026-07-28 client has no handshake, so
    injecting at the initialize-only seam would have advertised governance to
    exactly the generation that cannot see it (the shape of #605).

    Shared by both builders: it used to live only in ``MCPServerFactory``, which
    has no production call site, so the shipped ``serve --http`` advertised an
    empty ``capabilities.experimental`` (#595).
    """
    from mcp_hangar._sdk_compat import lowlevel_server

    server = lowlevel_server(mcp)
    extensions = governance_experimental_capabilities()
    if not extensions:
        return

    original = server.get_capabilities

    def _with_governance(*args: Any, **kwargs: Any) -> Any:
        capabilities = original(*args, **kwargs)
        merged: dict[str, Any] = dict(extensions)
        existing = getattr(capabilities, "experimental", None)
        if existing:
            merged.update(existing)
        return capabilities.model_copy(update={"experimental": merged})

    server.get_capabilities = _with_governance
