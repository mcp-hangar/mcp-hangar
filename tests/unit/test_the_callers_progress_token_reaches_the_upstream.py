"""The caller's progressToken is relayed and progress comes back (#883).

Recorded on the wire before this: a call whose caller had bound a progress
callback went upstream with ``traceparent`` and nothing else, so the upstream
was never asked for progress and the caller watched a six-second call block.
The relay mints an upstream token per call, asks the upstream with it, and
translates arriving ``notifications/progress`` back to the caller's token.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_hangar.domain.model.mcp_server import McpServer, _tool_call_params
from mcp_hangar.domain.services import progress_relay
from mcp_hangar.fastmcp_server.flat_tool_projection import _register_caller_progress_forwarder


@pytest.fixture(autouse=True)
def _clean_relay():
    yield
    progress_relay._forwarders.clear()


class TestRelayRegistry:
    def test_forward_reaches_the_registered_forwarder(self) -> None:
        seen = []
        progress_relay.register("tok-1", lambda p, t, m: seen.append((p, t, m)))

        claimed = progress_relay.forward({"progressToken": "tok-1", "progress": 3, "total": 10, "message": "step"})

        assert claimed is True
        assert seen == [(3.0, 10, "step")]

    def test_an_unknown_token_is_unclaimed(self) -> None:
        assert progress_relay.forward({"progressToken": "nobody", "progress": 1}) is False

    def test_unregister_is_blind_safe(self) -> None:
        progress_relay.unregister(None)
        progress_relay.unregister("never-registered")

    def test_the_wire_spelling_and_the_python_spelling_both_work(self) -> None:
        seen = []
        progress_relay.register("tok-2", lambda p, t, m: seen.append(p))
        assert progress_relay.forward({"progress_token": "tok-2", "progress": 1}) is True
        assert seen == [1.0]


class TestUpstreamAsk:
    def test_the_upstream_call_carries_the_minted_token(self) -> None:
        params = _tool_call_params("slow_tool", {"x": 1}, "hangar-progress-abc")
        assert params["_meta"] == {"progressToken": "hangar-progress-abc"}
        assert params["name"] == "slow_tool"

    def test_no_token_means_no_meta(self) -> None:
        """A caller that asked for nothing must not grow an empty _meta."""
        assert "_meta" not in _tool_call_params("t", {}, None)


class TestCallerForwarder:
    @pytest.mark.asyncio
    async def test_progress_is_translated_back_to_the_callers_token(self) -> None:
        session = MagicMock()
        session.send_progress_notification = AsyncMock()
        ctx = SimpleNamespace(meta={"progress_token": "caller-7"}, session=session, request_id="req-1")

        upstream_token = _register_caller_progress_forwarder(ctx)
        assert upstream_token is not None and upstream_token != "caller-7"

        claimed = await asyncio.to_thread(
            progress_relay.forward,
            {"progressToken": upstream_token, "progress": 5, "total": 10, "message": "halfway"},
        )
        assert claimed is True
        # The forwarder schedules cross-thread via run_coroutine_threadsafe;
        # give the loop real ticks until the delivery lands (3.14 needs >1).
        for _ in range(100):
            if session.send_progress_notification.await_count:
                break
            await asyncio.sleep(0.01)

        session.send_progress_notification.assert_awaited_once_with(
            "caller-7", 5.0, total=10, message="halfway", related_request_id="req-1"
        )
        progress_relay.unregister(upstream_token)

    @pytest.mark.asyncio
    async def test_a_caller_without_a_token_registers_nothing(self) -> None:
        ctx = SimpleNamespace(meta={}, session=MagicMock(), request_id=None)
        assert _register_caller_progress_forwarder(ctx) is None
        assert progress_relay._forwarders == {}

    @pytest.mark.asyncio
    async def test_the_v1_path_without_a_session_registers_nothing(self) -> None:
        ctx = SimpleNamespace(meta={"progress_token": "x"}, session=None)
        assert _register_caller_progress_forwarder(ctx) is None


class TestRouting:
    def test_an_upstream_progress_notification_is_handed_to_the_relay(self) -> None:
        server = MagicMock(spec=McpServer)
        server.mcp_server_id = "m"
        server._route_upstream_message = McpServer._route_upstream_message.__get__(server)

        with patch.object(progress_relay, "forward", return_value=True) as forward:
            server._route_upstream_message(
                {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progressToken": "t", "progress": 1}}
            )

        forward.assert_called_once_with({"progressToken": "t", "progress": 1})
