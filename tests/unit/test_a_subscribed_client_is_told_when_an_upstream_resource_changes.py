"""A subscribed client is told when an upstream resource changes (#1027, split from #889).

The gateway advertises ``resources.subscribe`` and the three ``listChanged``
flags the moment ``subscriptions/listen`` is served -- which ``MCPServer``
registers unconditionally -- and until this landed nothing ever published, so
every one of those flags was a promise with no publisher behind it.

Covered here: what a tenant is allowed to subscribe to, that the upstream is
subscribed once per URI and released with the last stream, that an update is
handed back under the projected URI, and that a stream never sees an event
from an upstream its tenant does not project.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from mcp.shared.subscriptions import ResourceUpdated, ToolsListChanged

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services import subscription_relay as sink
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import subscription_relay as sr


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def _clean_relay_state():
    """Every global this module keeps, restored -- it outlives one test otherwise."""
    yield
    sr._upstream_refs.clear()
    sr._bus._listeners.clear()
    sr._loop = None
    sink.clear_sink()


class _FakeLow:
    """The lowlevel seam: a handler mapping plus the two calls used on it."""

    def __init__(self, listen_registered: bool = True) -> None:
        self._request_handlers = {"subscriptions/listen": object()} if listen_registered else {}
        self.handlers: dict[str, object] = {}

    def add_request_handler(self, method, _params_type, handler):
        self._request_handlers[method] = handler
        self.handlers[method] = handler


class _FakeListenHandler:
    """Stands in for the SDK's: records what it was asked to honor, subscribes, waits."""

    def __init__(self, bus) -> None:
        self.bus = bus
        self.honored: list[str] | None = None
        self.opened = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _ctx, params):
        self.honored = list(params.notifications.resource_subscriptions or ())
        self.unsubscribe = self.bus.subscribe(lambda event: None)
        self.opened.set()
        await self.release.wait()
        self.unsubscribe()
        return "stream-done"


def _register(front_door: bool = True, listen_registered: bool = True) -> tuple[_FakeLow, _FakeListenHandler | None]:
    low = _FakeLow(listen_registered=listen_registered)
    handlers: list[_FakeListenHandler] = []

    def _make(bus):
        handlers.append(_FakeListenHandler(bus))
        return handlers[-1]

    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=front_door),
        patch("mcp_hangar.fastmcp_server.subscription_relay.lowlevel_server", return_value=low),
        patch("mcp.server.subscriptions.ListenHandler", _make),
    ):
        installed = sr.maybe_register_subscription_relay(MagicMock())

    assert installed is front_door
    return low, (handlers[0] if handlers else None)


class TestRegistration:
    def test_the_front_door_serves_a_tenant_scoped_listen(self) -> None:
        low, _handler = _register()

        assert "subscriptions/listen" in low.handlers

    def test_every_other_mode_withdraws_the_sdks_own_listen(self) -> None:
        """#888: the modern subscription flags derive from this handler alone."""
        low, _handler = _register(front_door=False)

        assert "subscriptions/listen" not in low._request_handlers

    def test_withdrawal_is_a_no_op_when_the_sdk_never_registered_one(self) -> None:
        low, _handler = _register(front_door=False, listen_registered=False)

        assert low._request_handlers == {}


class TestHonoredSubscriptions:
    def test_only_what_the_tenant_can_read_is_honored(self) -> None:
        def resolve(_tenant, uri):
            return ("server_a", "demo://blob/1") if uri == "hangar://server_a/demo://blob/1" else None

        with patch("mcp_hangar.fastmcp_server.resource_link_read_through._resolve_target", side_effect=resolve):
            targets = sr._honored_targets(
                "tenant:a",
                ["hangar://server_a/demo://blob/1", "hangar://server_b/secret://x", "hangar://server_a/demo://blob/1"],
            )

        assert targets == [("hangar://server_a/demo://blob/1", "server_a", "demo://blob/1")]

    def test_the_upstream_is_subscribed_once_and_released_with_the_last_stream(self) -> None:
        targets = [("hangar://server_a/demo://blob/1", "server_a", "demo://blob/1")]
        with patch.object(sr, "_relay_subscription") as relay:
            sr._acquire_upstream(targets)
            sr._acquire_upstream(targets)
            assert relay.call_args_list == [(("resources/subscribe", "server_a", "demo://blob/1"),)]

            sr._release_upstream(targets)
            assert len(relay.call_args_list) == 1  # a second stream still holds it
            sr._release_upstream(targets)

        assert relay.call_args_list[-1] == (("resources/unsubscribe", "server_a", "demo://blob/1"),)
        assert sr._upstream_refs == {}

    @pytest.mark.asyncio
    async def test_a_listen_stream_holds_its_upstream_subscription_for_its_lifetime(self) -> None:
        low, handler = _register()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch.object(
                sr,
                "_honored_targets",
                return_value=[("hangar://server_a/demo://blob/1", "server_a", "demo://blob/1")],
            ),
            patch.object(sr, "_relay_subscription") as relay,
        ):
            from mcp_types import SubscriptionFilter, SubscriptionsListenRequestParams

            params = SubscriptionsListenRequestParams(
                notifications=SubscriptionFilter(
                    resource_subscriptions=["hangar://server_a/demo://blob/1", "hangar://server_b/nope"]
                )
            )
            token = identity_context_var.set(_identity("tenant:a"))
            try:
                stream = asyncio.create_task(low.handlers["subscriptions/listen"](None, params))
                await asyncio.wait_for(handler.opened.wait(), timeout=2)
                assert handler.honored == ["hangar://server_a/demo://blob/1"]
                assert relay.call_args_list == [(("resources/subscribe", "server_a", "demo://blob/1"),)]

                handler.release.set()
                assert await asyncio.wait_for(stream, timeout=2) == "stream-done"
            finally:
                identity_context_var.reset(token)

        assert relay.call_args_list[-1] == (("resources/unsubscribe", "server_a", "demo://blob/1"),)


class TestFanOut:
    @pytest.mark.asyncio
    async def test_an_event_reaches_only_the_streams_that_project_its_upstream(self) -> None:
        bus = sr._UpstreamScopedBus()
        seen_a: list[object] = []
        seen_b: list[object] = []
        for upstreams, seen in ((frozenset({"server_a"}), seen_a), (frozenset({"server_b"}), seen_b)):
            token = sr._listen_upstreams.set(upstreams)
            try:
                bus.subscribe(seen.append)
            finally:
                sr._listen_upstreams.reset(token)

        assert await bus.publish_scoped(ToolsListChanged(), "server_a") is True

        assert seen_a == [ToolsListChanged()]
        assert seen_b == []

    @pytest.mark.asyncio
    async def test_a_raising_listener_does_not_starve_the_others(self) -> None:
        bus = sr._UpstreamScopedBus()
        seen: list[object] = []
        token = sr._listen_upstreams.set(frozenset({"server_a"}))
        try:
            bus.subscribe(MagicMock(side_effect=RuntimeError("boom")))
            bus.subscribe(seen.append)
        finally:
            sr._listen_upstreams.reset(token)

        await bus.publish_scoped(ToolsListChanged(), "server_a")

        assert seen == [ToolsListChanged()]


class TestPublishFromTheReaderThread:
    def test_an_update_is_published_under_the_projected_uri(self) -> None:
        """The client only ever saw `hangar://<upstream>/...`; the upstream's own URI would not resolve."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        seen: list[object] = []
        token = sr._listen_upstreams.set(frozenset({"server_a"}))
        try:
            sr._bus.subscribe(seen.append)
        finally:
            sr._listen_upstreams.reset(token)
        sr._loop = loop
        try:
            assert sr._publish("server_a", "notifications/resources/updated", {"uri": "demo://blob/1"}) is True
            for _ in range(200):
                if seen:
                    break
                threading.Event().wait(0.01)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
            loop.close()

        assert seen == [ResourceUpdated(uri="hangar://server_a/demo://blob/1")]

    def test_nothing_is_published_before_a_client_has_ever_listened(self) -> None:
        assert sr._publish("server_a", "notifications/resources/updated", {"uri": "demo://blob/1"}) is False

    def test_a_notification_that_carries_no_event_is_ignored(self) -> None:
        sr._loop = asyncio.new_event_loop()
        try:
            assert sr._publish("server_a", "notifications/message", {}) is False
        finally:
            sr._loop.close()


class TestUpstreamRouting:
    def test_the_upstream_router_hands_change_notifications_to_the_front_door(self) -> None:
        from mcp_hangar.domain.model.mcp_server import McpServer

        server = MagicMock(spec=McpServer)
        server.mcp_server_id = "server_a"
        forwarded: list[tuple[str, str, dict]] = []
        sink.register_sink(
            lambda mcp_server_id, method, params: bool(forwarded.append((mcp_server_id, method, params))) or True
        )

        McpServer._route_upstream_message(
            server, {"method": "notifications/resources/updated", "params": {"uri": "demo://blob/1"}}
        )

        assert forwarded == [("server_a", "notifications/resources/updated", {"uri": "demo://blob/1"})]

    def test_a_tools_list_changed_still_rediscovers_and_now_nudges_too(self) -> None:
        from mcp_hangar.domain.model.mcp_server import McpServer

        server = MagicMock(spec=McpServer)
        server.mcp_server_id = "server_a"
        forwarded: list[str] = []
        sink.register_sink(lambda _s, method, _p: bool(forwarded.append(method)) or True)

        McpServer._route_upstream_message(server, {"method": "notifications/tools/list_changed"})

        server._refresh_tools.assert_called_once_with()
        assert forwarded == ["notifications/tools/list_changed"]

    def test_forwarding_is_a_no_op_with_no_front_door_registered(self) -> None:
        assert sink.forward("server_a", "notifications/resources/updated", {"uri": "x"}) is False
