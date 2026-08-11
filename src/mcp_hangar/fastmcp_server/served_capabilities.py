"""Withdraw capabilities the gateway advertises but does not serve (#888).

The handshake advertised ``prompts`` and ``resources`` on every deployment and
then served neither: registering the official reference server -- 4 prompts, 7
resources, 2 templates, all unconditional -- and asking the gateway returned 0
of each. A conformant client reads ``{"prompts": []}`` as *this server has no
prompts*, not as *this gateway does not carry prompts*, so an upstream's whole
prompt and resource surface was invisible with no error anywhere.

Nothing hard-coded that claim, which is why it survived: ``initialize`` is
answered by the SDK from ``get_capabilities()``, which derives each capability
from whether the matching handler is registered -- and FastMCP registers the
prompt and resource handlers unconditionally at construction, empty or not.
There was never a ``True`` to delete.

So the fix is to make the claim follow the content. When nothing is registered,
the default handlers are removed, and both surfaces stop claiming the
capability: ``initialize`` (via ``get_capabilities``) and the SEP-2575
``server/discover`` result, which reads the same call by design (#605). The
methods themselves then answer method-not-found, which is the honest reply to a
client asking for a surface this server does not have.

Derived, not inverted: when the proxy for upstream prompts and resources lands
(#889) it registers real handlers, ``_serves_*`` sees them, nothing is
withdrawn, and the capabilities come back on their own.

SDK seam used
-------------
``Server._request_handlers`` -- the same mapping ``get_capabilities`` reads to
decide what to advertise, keyed by method name. There is a public
``add_request_handler`` and a public ``get_request_handler``, but no public
removal, so the mapping is edited directly. This is the same class of seam
``flat_tool_projection`` already relies on for the front-door handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_hangar._sdk_compat import FastMCP, lowlevel_server

logger = logging.getLogger(__name__)

#: Methods the SDK registers for prompts. Withdrawn together: a server that
#: cannot list prompts has nothing for ``prompts/get`` to fetch either.
PROMPT_METHODS = ("prompts/list", "prompts/get")

#: Methods the SDK registers for resources, plus the two subscription methods.
#: Subscription is advertised as ``resources.subscribe`` on the same capability
#: object, so it has to go with the rest or the object stays half-true.
RESOURCE_METHODS = (
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "resources/subscribe",
    "resources/unsubscribe",
)


def _serves_prompts(mcp: FastMCP) -> bool | None:
    """Whether any prompt is registered. ``None`` when it cannot be determined."""
    manager = getattr(mcp, "_prompt_manager", None)
    if manager is None or not hasattr(manager, "list_prompts"):
        return None
    return bool(manager.list_prompts())


def _serves_resources(mcp: FastMCP) -> bool | None:
    """Whether any resource or template is registered. ``None`` when undeterminable."""
    manager = getattr(mcp, "_resource_manager", None)
    if manager is None or not hasattr(manager, "list_resources") or not hasattr(manager, "list_templates"):
        return None
    return bool(manager.list_resources()) or bool(manager.list_templates())


def _withdraw(handlers: dict[str, Any], methods: tuple[str, ...]) -> list[str]:
    """Remove *methods* from the handler mapping, returning the ones that were there."""
    return [method for method in methods if handlers.pop(method, None) is not None]


def withdraw_unserved_capabilities(mcp: FastMCP) -> tuple[str, ...]:
    """Stop advertising ``prompts`` / ``resources`` while nothing is registered.

    Fails towards the status quo on purpose. If the SDK's shape changes and
    emptiness cannot be established, the capability is left advertised and a
    warning is logged: wrongly withdrawing a capability that IS served would
    make a working surface unreachable, which is worse than continuing to
    over-promise an empty one.

    Args:
        mcp: The server to inspect and edit, before it starts serving.

    Returns:
        The method names withdrawn, in registration groups' order. Empty when
        everything advertised is served -- or when nothing could be decided.
    """
    try:
        handlers = lowlevel_server(mcp)._request_handlers
    except Exception:  # noqa: BLE001 -- fault-barrier: never fail startup over an advertisement
        logger.warning("served_capabilities_seam_unavailable", exc_info=True)
        return ()

    withdrawn: list[str] = []
    for kind, serves, methods in (
        ("prompts", _serves_prompts(mcp), PROMPT_METHODS),
        ("resources", _serves_resources(mcp), RESOURCE_METHODS),
    ):
        if serves is None:
            logger.warning(
                "served_capabilities_undeterminable capability=%s -- leaving it advertised",
                kind,
            )
            continue
        if not serves:
            withdrawn.extend(_withdraw(handlers, methods))

    if withdrawn:
        logger.debug("served_capabilities_withdrawn methods=%s", withdrawn)
    return tuple(withdrawn)
