"""The front door answers an upstream's ``-32021`` as the caller's own, when it is true of the caller (#2315).

2026-07-28 (SEP-2575): a server that needs a capability the client did not
include in ``io.modelcontextprotocol/clientCapabilities`` MUST answer
``MissingRequiredClientCapabilityError`` (``-32021``), with the missing ones as a
``ClientCapabilities`` object in ``data.requiredCapabilities``, on HTTP 400. To
the caller the front door is that server, so an upstream's ``-32021`` is relayed
only when it is a true statement about the caller. That means every capability
it names is one the caller left out of its declaration.

That is not given. Hangar does not forward the caller's declaration upstream:
it declares sampling, elicitation and roots to no upstream, and the Tasks
extension only for a caller that declared it
(:func:`~mcp_hangar.protocol.forwardable_client_capabilities`). An upstream that
needs sampling says ``-32021`` even to a caller that declared it. Telling that
caller to declare something it already declared would be false, so it gets the
tool error it got before.

Everything else gets that tool error too, the only way to fail here. That
covers a caller that does not speak 2026-07-28 (``-32021`` is not in its
revision), a declaration that cannot be read, a capability the caller declared
in any form, a family the SDK does not model (it drops one from the
declaration, so whether the caller declared it cannot be told), and a
capability named under ``io.mcp-hangar/``, which no upstream may speak for.
The ``data`` relayed is ``requiredCapabilities`` and nothing else. The domain
dropped every other field (``_missing_capability_error``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .._sdk_compat import is_modern_protocol_version, make_mcp_error

#: The capability families whose members are named one level down.
_NAMED_MEMBERS = ("extensions", "experimental")

#: Hangar's own namespace. An upstream naming it is not relayed (ADR-029 s12.3).
_HANGAR_PREFIX = "io.mcp-hangar/"


def _declared(ctx: Any) -> tuple[Mapping[str, Any], frozenset[str]] | None:
    """The caller's declared capabilities as JSON, and the families they can hold; None when unreadable.

    None too when the caller is not 2026-07-28. The SDK's ``ClientCapabilities``
    keeps only the families it models: a family it does not model is dropped
    from the declaration whether or not the caller declared it, so only the
    modelled ones can be judged.
    """
    session = getattr(ctx, "session", None)
    version = getattr(session, "protocol_version", None)
    if not isinstance(version, str) or not is_modern_protocol_version(version):
        return None
    capabilities = getattr(session, "client_capabilities", None)
    if capabilities is None:
        return None
    dumped = capabilities.model_dump(by_alias=True, mode="json", exclude_none=True)
    fields = type(capabilities).model_fields
    families = frozenset(field.alias or name for name, field in fields.items())
    return (dumped, families) if isinstance(dumped, dict) else None


def _names(required: Mapping[str, Any]) -> list[tuple[str, str | None]] | None:
    """Each capability *required* names, as (family, member); None when it is not a ClientCapabilities object."""
    names: list[tuple[str, str | None]] = []
    for family, value in required.items():
        if not isinstance(value, dict):
            return None
        if family in _NAMED_MEMBERS:
            if not value or not all(isinstance(member, dict) for member in value.values()):
                return None
            names.extend((family, member) for member in value)
        else:
            names.append((family, None))
    return names or None


def _is_hangars(family: str, member: str | None) -> bool:
    return family.startswith(_HANGAR_PREFIX) or (member is not None and member.startswith(_HANGAR_PREFIX))


def _left_out(declared: Mapping[str, Any], family: str, member: str | None) -> bool:
    """Whether the caller's declaration has nothing of (*family*, *member*)."""
    if member is None:
        return family not in declared
    declared_family = declared.get(family)
    return not isinstance(declared_family, dict) or member not in declared_family


def relayed_missing_capability(upstream: dict[str, Any] | None, ctx: Any) -> Exception | None:
    """The ``-32021`` to answer the caller with, or None to answer with the tool error.

    *upstream* is the error the aggregate kept (code, message,
    ``data.requiredCapabilities``); *ctx* is the SDK's request context of the
    caller's ``tools/call``. Raised from the handler, the SDK sends it on HTTP
    400 (``mcp.shared.inbound.ERROR_CODE_HTTP_STATUS``).
    """
    if upstream is None:
        return None
    try:
        read = _declared(ctx)
        required = upstream["data"]["requiredCapabilities"]
        names = _names(required) if isinstance(required, dict) else None
        if read is None or names is None:
            return None
        declared, families = read
        for family, member in names:
            if family not in families or _is_hangars(family, member) or not _left_out(declared, family, member):
                return None
        data = {"requiredCapabilities": required}
        error: Exception = make_mcp_error(upstream["code"], upstream["message"], data=data)
        return error
    except Exception:  # noqa: BLE001 -- anything unreadable is the tool error the caller got before
        return None
