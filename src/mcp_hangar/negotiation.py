"""Inbound protocol negotiation for the stateless MCP model (SEP-2575).

The 2026-07-28 revision has no ``initialize`` handshake, so a client conveys its
``protocolVersion`` and ``capabilities`` in ``params._meta`` on *every* request
rather than establishing them once for a session. This module is the inbound
counterpart to :mod:`mcp_hangar.protocol` (which *injects* the same keys on the
outbound path): it *reads* those keys off each inbound request, fail-safe, and
exposes the result per request via a contextvar so downstream code can gate on
the negotiated version/capabilities.

This is an additive read path: it never gates or rejects and never raises on a
missing or malformed ``_meta`` -- it falls back to
:data:`~mcp_hangar.protocol.SUPPORTED_PROTOCOL_VERSION` and empty capabilities.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .protocol import (
    _META_CAPABILITIES_KEY,
    _META_PROTOCOL_VERSION_KEY,
    SUPPORTED_PROTOCOL_VERSION,
)

_EMPTY_CAPABILITIES: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class ProtocolNegotiation:
    """The protocol version + client capabilities negotiated for one request.

    ``protocol_version`` always holds a usable value: the client-supplied version
    when present and well-formed, otherwise
    :data:`~mcp_hangar.protocol.SUPPORTED_PROTOCOL_VERSION`. ``capabilities`` is an
    empty mapping when the client advertised none.
    """

    protocol_version: str = SUPPORTED_PROTOCOL_VERSION
    capabilities: Mapping[str, Any] = field(default=_EMPTY_CAPABILITIES)


def read_protocol_negotiation(meta: Mapping[str, Any] | None) -> ProtocolNegotiation:
    """Extract the negotiated protocol version + capabilities from ``_meta``.

    Reads the reverse-DNS keys ``io.modelcontextprotocol/protocolVersion`` and
    ``io.modelcontextprotocol/capabilities`` from the inbound request's
    ``params._meta`` mapping. Fully fail-safe: a ``None``, empty, or garbage
    ``meta`` (or any read error) yields the default version and empty
    capabilities, and this function never raises.
    """
    if not meta:
        return ProtocolNegotiation()

    try:
        raw_version = meta.get(_META_PROTOCOL_VERSION_KEY)
        version = raw_version if isinstance(raw_version, str) and raw_version else SUPPORTED_PROTOCOL_VERSION

        raw_caps = meta.get(_META_CAPABILITIES_KEY)
        capabilities: Mapping[str, Any]
        if isinstance(raw_caps, Mapping):
            capabilities = MappingProxyType(dict(raw_caps))
        else:
            capabilities = _EMPTY_CAPABILITIES

        return ProtocolNegotiation(protocol_version=version, capabilities=capabilities)
    except Exception:  # noqa: BLE001 -- fault barrier: negotiation must never break a request
        return ProtocolNegotiation()


# Request-scoped negotiation, mirroring the identity/tool-pin contextvars. Set on
# the inbound path and inherited by batch worker threads via copy_context().
_protocol_negotiation_var: ContextVar[ProtocolNegotiation | None] = ContextVar("protocol_negotiation", default=None)


def set_current_protocol_negotiation(negotiation: ProtocolNegotiation) -> None:
    """Store the negotiated protocol context for the current request scope."""
    _protocol_negotiation_var.set(negotiation)


def get_current_protocol_negotiation() -> ProtocolNegotiation | None:
    """Return the negotiation read for the current request, or ``None`` if unset."""
    return _protocol_negotiation_var.get()
