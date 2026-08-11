"""Shared MCP protocol context for the outbound path.

Leaf module (no internal imports) so both the domain startup handshake and the
transport clients can use these without crossing layer boundaries or risking an
import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any

# MCP protocol version Hangar advertises to upstream MCP servers. Targets the
# 2026-07-28 revision; a legacy upstream downgrades in its initialize response.
SUPPORTED_PROTOCOL_VERSION = "2026-07-28"

# OUTBOUND client identity, and the counterpart to the INBOUND
# ``config.HANGAR_SERVER_NAME`` that #560 unified. This said
# ``mcp-registry / 1.0.0`` -- a product name that has not existed for a long
# time, at a literal version that never moved off 1.0.0 while the gateway
# sending it was 2.5.2. It is what an upstream operator has in their logs when
# working out who is calling them, and it rides `_meta` on EVERY modern request
# (see ``inject_protocol_meta``), not only the handshake.
#
# Read from package metadata rather than restated, so it cannot drift again.
# Resolved once at import: the installed version does not change under a
# running process, and the same PackageNotFoundError fallback as
# ``mcp_hangar.__init__`` keeps a source checkout working.
HANGAR_CLIENT_NAME = "mcp-hangar"


def _package_version() -> str:
    try:
        return version("mcp-hangar")
    except PackageNotFoundError:  # pragma: no cover -- source checkout without an install
        return "0.0.0.dev"


# clientInfo Hangar presents to upstream servers.
HANGAR_CLIENT_INFO = {"name": HANGAR_CLIENT_NAME, "version": _package_version()}

# Reverse-DNS _meta keys per the MCP spec namespace (SEP-2575 stateless model).
_META_PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_META_CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
# The spec key for client capabilities. It is `clientCapabilities`, NOT
# `capabilities`: the SDK's inbound ladder requires
# `_meta["io.modelcontextprotocol/clientCapabilities"]` on every modern request,
# and `io.modelcontextprotocol/capabilities` appears nowhere in `mcp_types`.
# Hangar read the latter, so `read_protocol_negotiation` returned empty
# capabilities for every well-formed request -- silently, because nothing
# consumed them until now.
_META_CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"

# The key Hangar used to read. Kept only so a caller that copied the old
# (non-spec) spelling is still understood; the spec key wins.
_META_CAPABILITIES_KEY_LEGACY = "io.modelcontextprotocol/capabilities"

#: The Tasks extension identifier, as declared under `clientCapabilities.extensions`.
TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"


def inject_protocol_meta(params: dict[str, Any], *, modern_envelope: bool = True) -> dict[str, Any]:
    """Return ``params`` with Hangar's protocol context merged into ``params._meta``.

    A stateless upstream (SEP-2575) has no initialize handshake, so the protocol
    version + client info must travel in every request's ``_meta`` instead. This
    returns a new dict and does not mutate the caller's ``params``; existing
    ``_meta`` keys are preserved and caller-set protocol keys win (set-if-absent).

    ``modern_envelope=False`` omits those keys, and that is not an optimisation.
    From ``mcp==2.0.0`` the SDK enforces era separation: a connection that
    negotiated a legacy version at ``initialize`` rejects **every** subsequent
    request carrying the 2026-07-28 envelope with ``-32600`` ("this connection
    serves the handshake protocol era"). Hangar stamped the envelope
    unconditionally, so against any SDK-built legacy upstream discovery failed,
    the cold start never completed, and the batch timed out -- a hang rather than
    an error. The beta tolerated it; the stable release does not.

    Trace context is separate and always injected by the caller, so an upstream
    that ignores ``_meta`` is unaffected either way.

    Also forwards the **caller's** Tasks declaration, per request, when Hangar can
    honestly stand behind it -- see :func:`forwardable_client_capabilities`. That
    is what makes the governed relay reachable at all: SEP-2663 leaves
    augmentation to the upstream and gates it on the *caller* having declared the
    extension, so an upstream that follows the spec never mints a task for a
    client that declared nothing. Hangar is that client on the wire.
    """
    meta = dict(params.get("_meta") or {})
    if not modern_envelope:
        # `_meta` still has to EXIST: it is also the trace-context carrier, and
        # the caller injects into it immediately after this returns. Only the
        # protocol keys are withheld -- the era gate is about those, not about
        # `_meta` as such.
        return {**params, "_meta": meta}

    meta.setdefault(_META_PROTOCOL_VERSION_KEY, SUPPORTED_PROTOCOL_VERSION)
    meta.setdefault(_META_CLIENT_INFO_KEY, dict(HANGAR_CLIENT_INFO))

    capabilities = forwardable_client_capabilities()
    if capabilities is not None:
        meta.setdefault(_META_CLIENT_CAPABILITIES_KEY, capabilities)
    return {**params, "_meta": meta}


#: Whether the ADR-014 governed task relay is actually serving in this process.
#:
#: A boot-time fact, written once by the single seam that activates the relay
#: (``fastmcp_server.task_relay_wiring.enable_governed_task_relay``), so the two
#: cannot disagree. This module used to answer the question by importing
#: ``server.context`` and reading ``ctx.governed_task_store`` -- a leaf protocol
#: module reaching three layers up into delivery for application state.
_task_relay_wired = False


def set_task_relay_wired(wired: bool) -> None:
    """Record whether the governed task relay is serving. Called by the wiring seam."""
    global _task_relay_wired
    _task_relay_wired = wired


def is_task_relay_wired() -> bool:
    """Whether Hangar can honestly stand behind a forwarded Tasks declaration."""
    return _task_relay_wired


def forwardable_client_capabilities() -> dict[str, Any] | None:
    """The caller's declared capabilities Hangar may relay upstream, or ``None``.

    Deliberately **not** a passthrough of whatever the caller declared, and not a
    blanket claim either. Two conditions must both hold, and each excludes a
    concrete way of lying:

    * **The caller declared the Tasks extension.** A connection-level claim would
      let an upstream mint a task for a client that never asked for one -- and
      that client is then answered ``-32021`` on ``tasks/get``, holding a handle
      it cannot use. Per-request tracking keeps the two ends consistent; SEP-2663
      provides exactly this opt-in for the purpose.
    * **Hangar's relay is actually wired.** With the kill-switch off there is no
      governed store and no ``tasks/*`` surface, so claiming the capability would
      promise governance that is not running.

    Fault-barriered: any failure yields ``None``, which degrades to the previous
    behaviour (declare nothing) rather than breaking an invoke.
    """
    try:
        from .negotiation import get_current_protocol_negotiation

        negotiation = get_current_protocol_negotiation()
        if negotiation is None:
            return None
        extensions = negotiation.capabilities.get("extensions")
        if not isinstance(extensions, Mapping) or TASKS_EXTENSION_ID not in extensions:
            return None

        if not is_task_relay_wired():
            return None
    except Exception:  # noqa: BLE001 -- never break an invoke over a capability read
        return None

    return {"extensions": {TASKS_EXTENSION_ID: {}}}


#: JSON-RPC code for "the upstream rejected our transport session". Matches what
#: the SDK client reports for the same condition (`INVALID_REQUEST` +
#: "Session terminated"), so a caller already handling the SDK's shape handles
#: ours.
SESSION_TERMINATED_CODE = -32600

#: Machine-readable discriminator carried in that error's ``data``. Callers must
#: key on this rather than the message, which is prose. Lives here, in the leaf
#: protocol module, so the domain layer can recognise the condition without
#: importing a transport client.
SESSION_TERMINATED_REASON = "mcp_session_terminated"
