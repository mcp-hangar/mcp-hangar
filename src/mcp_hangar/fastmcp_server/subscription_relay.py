"""Front-door ``subscriptions/listen``, and the upstream updates it delivers (#1027, split from #889).

What a client subscribes to
---------------------------
Not ``resources/subscribe``. On the 2026-07-28 wire (SEP-2575) there is no
standing GET stream a server can push on: a client opts in with
``subscriptions/listen``, whose *response is the stream*, and the change
notifications -- ``notifications/resources/updated`` and the three
``*/list_changed`` -- ride that stream and nowhere else. The SDK still carries
the legacy ``resources/subscribe`` handler, but a modern connection has no
channel to deliver its updates on (``NotifyOnlyOutbound`` drops them), so
serving it would accept a subscription that can never fire. The issue was
written against the older wire; this is the same feature on the wire we serve.

The SDK's :class:`~mcp.server.subscriptions.ListenHandler` already does the
per-stream work -- acknowledge the honored filter first, stamp every frame with
the subscription id, bound the backlog. What it cannot do is tenancy, so the
handler is wrapped rather than replaced:

* the honored ``resource_subscriptions`` are filtered to the projected URIs
  this tenant can actually resolve and read (``_resolve_target``, so the same
  governance decision as ``resources/read`` -- denied means unsubscribable as
  well as absent), and the ack tells the client what survived;
* each stream is tagged with the upstream ids its tenant projected when it
  opened, and :class:`_UpstreamScopedBus` delivers an event only to streams
  carrying its upstream. A ``ResourceUpdated`` is already tenant-safe (its
  projected ``hangar://<upstream>/...`` URI had to be honored first), but a
  bare ``*ListChanged`` carries no URI to filter on, and without the tag every
  tenant would learn that another tenant's upstream had changed;
* the upstream is subscribed once per URI however many streams ask
  (``_upstream_refs``), and unsubscribed when the last one goes away.

An upstream that refuses ``resources/subscribe`` (or never sends updates) costs
the stream nothing: the subscription stays honored and never fires, exactly as
a subscription to a URI that never changes does.

Why it also publishes the ``*/list_changed`` events
---------------------------------------------------
``get_capabilities`` derives all four modern flags -- ``resources.subscribe``
and the three ``listChanged`` -- from one fact: whether ``subscriptions/listen``
is served. There is no way to advertise the subscription without advertising
the nudges, so the honest choice is to publish the nudges, which cost one line
each on the upstream router that already receives them. The same rule is why
:func:`maybe_register_subscription_relay` *withdraws* the handler outside
``front_door`` mode, where nothing publishes at all (#888, derived not
inverted).

Caveats: sessions are per-replica (#877), so a subscription dies with its pod
exactly as its session does; and the upstream half needs an upstream that
offers the GET stream, so a modern upstream (which would need Hangar to open a
``subscriptions/listen`` of its own) delivers nothing yet.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from collections.abc import Callable
from typing import Any

from mcp_hangar._sdk_compat import lowlevel_server

from ..domain.services import subscription_relay as relay_sink

logger = logging.getLogger(__name__)

#: The upstream ids the tenant of the listen stream being opened projects.
#: Set around the delegation so the bus can tag each listener as it subscribes.
_listen_upstreams: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "hangar_listen_upstreams", default=frozenset()
)

_lock = threading.Lock()

#: ``(upstream id, upstream uri)`` -> how many live listen streams hold it.
_upstream_refs: dict[tuple[str, str], int] = {}

#: The serving event loop, captured on the first listen stream. Upstream events
#: arrive on the GET stream's reader thread and the bus's fan-out touches
#: per-stream buffers that belong to this loop, so publishing crosses over the
#: same way the progress relay does (#883).
_loop: asyncio.AbstractEventLoop | None = None


class _UpstreamScopedBus:
    """``SubscriptionBus`` that delivers an event only to streams that project its upstream.

    Listener registration order is delivery order, as in the SDK's in-memory
    bus. A raising listener is logged and skipped so one bad stream cannot
    starve the others.
    """

    def __init__(self) -> None:
        # Keyed by a per-subscription token so the same callable may register
        # more than once (bound methods compare equal).
        self._listeners: dict[object, tuple[frozenset[str], Callable[[Any], None]]] = {}

    def subscribe(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        """Register *listener*, tagged with the opening stream's upstream ids."""
        token = object()
        self._listeners[token] = (_listen_upstreams.get(), listener)

        def unsubscribe() -> None:
            self._listeners.pop(token, None)

        return unsubscribe

    async def publish(self, event: Any) -> None:
        """``SubscriptionBus`` entry point: an event with no upstream reaches everyone."""
        await self.publish_scoped(event, None)

    async def publish_scoped(self, event: Any, mcp_server_id: str | None) -> bool:
        """Fan *event* out to the streams entitled to it; whether any took it."""
        import anyio.lowlevel

        delivered = False
        for upstreams, listener in list(self._listeners.values()):
            if mcp_server_id is not None and mcp_server_id not in upstreams:
                continue
            try:
                listener(event)
            except Exception:  # noqa: BLE001 -- fan-out boundary: isolate listeners from each other
                logger.exception("subscription listener raised; continuing")
            delivered = True
        # Let the streams drain between events instead of overflowing unread.
        await anyio.lowlevel.checkpoint()
        return delivered


_bus = _UpstreamScopedBus()


def _tenant() -> str | None:
    from ..context import get_identity_context

    identity = get_identity_context()
    return identity.caller.tenant_id if identity is not None else None


def _honored_targets(tenant_id: str | None, requested: list[str]) -> list[tuple[str, str, str]]:
    """``(projected uri, upstream id, upstream uri)`` for what this tenant may subscribe to.

    A URI this tenant cannot resolve or read is dropped rather than refused:
    the ack names what was honored, which is how a client learns the answer
    without learning whether the URI exists for somebody else.
    """
    from .resource_link_read_through import _resolve_target

    targets: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for uri in requested:
        if not isinstance(uri, str) or uri in seen:
            continue
        seen.add(uri)
        target = _resolve_target(tenant_id, uri)
        if target is None:
            logger.debug("listen_subscription_dropped tenant=%s uri=%s", tenant_id, uri)
            continue
        targets.append((uri, *target))
    return targets


def _relay_subscription(method: str, mcp_server_id: str, upstream_uri: str) -> None:
    """Ask the upstream to start/stop sending updates for one of its own URIs."""
    from .prompt_proxy import _relay

    try:
        response = _relay(mcp_server_id, method, {"uri": upstream_uri})
    except Exception:  # noqa: BLE001 -- an upstream that cannot subscribe costs the stream nothing
        logger.debug("upstream_subscription_relay_failed method=%s mcp_server=%s", method, mcp_server_id, exc_info=True)
        return
    if "error" in response:
        logger.debug(
            "upstream_subscription_refused method=%s mcp_server=%s error=%s",
            method,
            mcp_server_id,
            response["error"],
        )


def _acquire_upstream(targets: list[tuple[str, str, str]]) -> None:
    """Subscribe upstream for each target, once however many streams hold it."""
    for _projected, mcp_server_id, upstream_uri in targets:
        key = (mcp_server_id, upstream_uri)
        with _lock:
            held = _upstream_refs.get(key, 0)
            _upstream_refs[key] = held + 1
        if held == 0:
            _relay_subscription("resources/subscribe", mcp_server_id, upstream_uri)


def _release_upstream(targets: list[tuple[str, str, str]]) -> None:
    """Drop this stream's hold, unsubscribing upstream when it was the last."""
    for _projected, mcp_server_id, upstream_uri in targets:
        key = (mcp_server_id, upstream_uri)
        with _lock:
            held = _upstream_refs.get(key, 0)
            if held <= 1:
                _upstream_refs.pop(key, None)
            else:
                _upstream_refs[key] = held - 1
        if held <= 1:
            _relay_subscription("resources/unsubscribe", mcp_server_id, upstream_uri)


def _publish(mcp_server_id: str, method: str, params: dict[str, Any]) -> bool:
    """Sink for the upstream router: publish one upstream notification (#1027).

    Runs on the GET stream's reader thread. Returns whether it was handed to
    the serving loop at all -- ``False`` before any client has ever listened,
    which is the ordinary answer and is logged as unclaimed by the caller.
    """
    from mcp.shared.subscriptions import ResourceUpdated, event_from_wire

    from .resource_link_read_through import project_uri

    loop = _loop
    if loop is None:
        return False
    event = event_from_wire(method, params)
    if event is None:
        return False
    if isinstance(event, ResourceUpdated):
        # A client only ever saw the projected URI, and only a stream that
        # honored that exact URI is entitled to the update.
        event = ResourceUpdated(uri=project_uri(mcp_server_id, event.uri))
    asyncio.run_coroutine_threadsafe(_bus.publish_scoped(event, mcp_server_id), loop)
    return True


def maybe_register_subscription_relay(mcp: Any) -> bool:
    """Install the tenant-scoped ``subscriptions/listen`` surface in ``front_door`` mode.

    Outside front_door mode the SDK's own handler is *withdrawn* instead:
    ``MCPServer`` registers one unconditionally, and every modern subscription
    flag is derived from its presence, so leaving it in place advertises
    updates nothing in this process ever publishes (#888).

    Returns whether the front-door handler was installed.
    """
    from ..domain.services.tool_access_resolver import is_front_door

    low = lowlevel_server(mcp)
    if hasattr(low, "list_tools"):  # SDK v1 surface: no listen wire at all
        return False

    if not is_front_door():
        try:
            withdrawn = low._request_handlers.pop("subscriptions/listen", None)
        except Exception:  # noqa: BLE001 -- fault-barrier: never fail startup over an advertisement
            logger.warning("subscription_listen_seam_unavailable", exc_info=True)
            return False
        if withdrawn is not None:
            logger.debug("subscription_listen_withdrawn (topology_mode!=front_door)")
        return False

    from mcp.server.subscriptions import ListenHandler
    from mcp_types import SubscriptionsListenRequestParams

    from .asgi import bind_caller_identity, release_caller_identity
    from .prompt_proxy import _upstream_ids

    handler = ListenHandler(_bus)

    async def _listen(ctx: Any, params: Any) -> Any:
        global _loop

        token = bind_caller_identity(ctx)
        try:
            _loop = asyncio.get_running_loop()
            tenant_id = _tenant()
            upstreams = frozenset(await asyncio.to_thread(_upstream_ids, tenant_id))
            requested = list(params.notifications.resource_subscriptions or ())
            targets = await asyncio.to_thread(_honored_targets, tenant_id, requested)
            scoped = params.model_copy(
                update={
                    "notifications": params.notifications.model_copy(
                        update={"resource_subscriptions": [projected for projected, _s, _u in targets] or None}
                    )
                }
            )
            await asyncio.to_thread(_acquire_upstream, targets)
            upstream_token = _listen_upstreams.set(upstreams)
            try:
                return await handler(ctx, scoped)
            finally:
                _listen_upstreams.reset(upstream_token)
                await asyncio.to_thread(_release_upstream, targets)
        finally:
            release_caller_identity(token)

    low.add_request_handler("subscriptions/listen", SubscriptionsListenRequestParams, _listen)
    relay_sink.register_sink(_publish)
    logger.info("subscription_relay_registered (topology_mode=front_door)")
    return True
