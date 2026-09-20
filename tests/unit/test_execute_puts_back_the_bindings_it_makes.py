"""``BatchExecutor.execute`` puts back every request-scoped binding it makes (#1503).

``execute`` binds this request's SEP-2243 routing headers so an L7 selector can
read them from a worker thread, and the negotiated protocol version beside them.
Both were bound and never reset. Both callers reached ``execute`` through
``asyncio.to_thread``, on a copied context that is discarded, so nothing carried
over -- until a caller runs it on a context it keeps, which is what the front
door's flat path now does.

These tests drive two calls through one context and read what a worker sees, with
the real evaluator rather than a stub: the selector that matched the first call's
header must not decide the second, and the context must end as it began --
including when the batch raises.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.context import get_routing_headers, routing_headers_var
from mcp_hangar.domain.policies.egress_l7 import (
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
    evaluate,
)
from mcp_hangar.negotiation import get_current_protocol_negotiation
from mcp_hangar.protocol import _META_PROTOCOL_VERSION_KEY, SUPPORTED_PROTOCOL_VERSION
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

MODERN = "2026-07-28"
CLIENT_VERSION = "2026-03-26"

#: Every tool is allowed, and one region is routed to a human. A verdict that
#: differs by region is how a test can tell whose headers were read.
POLICY = L7Policy(
    tools=ToolRules(allow=("*",)),
    headers=HeaderRules(require_approval=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)),
)


def _request_ctx(region: str) -> SimpleNamespace:
    """A FastMCP request context for a POST carrying one ``Mcp-Param-*`` header."""
    request = SimpleNamespace(
        headers={"mcp-param-region": region, "mcp-protocol-version": MODERN},
        state=SimpleNamespace(),
    )
    return SimpleNamespace(
        request_context=SimpleNamespace(meta={_META_PROTOCOL_VERSION_KEY: CLIENT_VERSION}, request=request)
    )


def _call_spec() -> CallSpec:
    return CallSpec(index=0, call_id="c0", mcp_server="math", tool="add", arguments={"a": 1})


@pytest.fixture()
def mock_context():
    """Minimal ApplicationContext mock so the real executor path runs in-process."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.mcp_server_exists.return_value = True

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        exec_groups.get.return_value = None
        yield ctx


def _run(mock_context: Any, region: str) -> list[dict[str, Any]]:
    """Execute one batch call and report what its worker thread read."""
    seen: list[dict[str, Any]] = []

    def _spy_send(_cmd: Any) -> dict[str, Any]:
        headers = get_routing_headers()
        negotiation = get_current_protocol_negotiation()
        seen.append(
            {
                "region": (headers or {}).get("mcp-param-region"),
                # The real L7 evaluator, on the input the aggregate gives it.
                "action": evaluate("add", {"a": 1}, POLICY, headers).action,
                "version": negotiation.protocol_version if negotiation is not None else None,
            }
        )
        return {"ok": True}

    mock_context.command_bus.send.side_effect = _spy_send
    result = BatchExecutor().execute(
        batch_id=f"b-{region}",
        calls=[_call_spec()],
        max_concurrency=2,
        global_timeout=30.0,
        fail_fast=False,
        request_ctx=_request_ctx(region),
    )

    assert result.success is True, f"batch failed: {result}"
    return seen


class TestTwoCallsOnOneContext:
    def test_each_call_reads_its_own_headers(self, mock_context):
        """The second call routes on its own region, not the first one's."""
        first = _run(mock_context, "eu-west-1")
        second = _run(mock_context, "us-east-1")

        assert [s["region"] for s in first] == ["eu-west-1"]
        assert [s["region"] for s in second] == ["us-east-1"]

    def test_the_l7_selector_decides_per_call(self, mock_context):
        """A selector that matched the first call's header must not decide the second."""
        first = _run(mock_context, "eu-west-1")
        second = _run(mock_context, "us-east-1")

        assert first[0]["action"] is ToolAction.REQUIRE_APPROVAL
        assert second[0]["action"] is ToolAction.ALLOW

    def test_the_negotiated_version_is_the_calls_own(self, mock_context):
        """The negotiation bound beside the headers is reset with them."""
        assert _run(mock_context, "eu-west-1")[0]["version"] == CLIENT_VERSION
        assert get_current_protocol_negotiation() is None

    def test_nothing_is_left_bound(self, mock_context):
        """Two calls, and the context ends as it began: unbound."""
        _run(mock_context, "eu-west-1")
        _run(mock_context, "us-east-1")

        assert get_routing_headers() is None
        assert get_current_protocol_negotiation() is None

    def test_the_binding_that_was_there_before_is_put_back(self, mock_context):
        """A caller's own binding is restored, not cleared."""
        outer = {"mcp-param-region": "ap-south-1"}
        token = routing_headers_var.set(outer)
        try:
            assert _run(mock_context, "eu-west-1")[0]["region"] == "eu-west-1"
            assert get_routing_headers() == outer
        finally:
            routing_headers_var.reset(token)


class TestWhenTheCallRaises:
    def test_a_raising_batch_leaves_nothing_bound(self, mock_context):
        """The reset is in a finally, so a failure on the way out still unbinds."""
        mock_context.event_bus.publish.side_effect = RuntimeError("bus is down")

        with pytest.raises(RuntimeError, match="bus is down"):
            BatchExecutor().execute(
                batch_id="b-raises",
                calls=[_call_spec()],
                max_concurrency=2,
                global_timeout=30.0,
                fail_fast=False,
                request_ctx=_request_ctx("eu-west-1"),
            )

        assert get_routing_headers() is None
        assert get_current_protocol_negotiation() is None


class TestTheNoRequestPath:
    def test_stdio_binds_nothing_and_resets_cleanly(self, mock_context):
        """``request_ctx=None``: no headers to bind, the default version, no leak."""
        seen: list[Any] = []

        def _spy_send(_cmd: Any) -> dict[str, Any]:
            negotiation = get_current_protocol_negotiation()
            seen.append((get_routing_headers(), negotiation.protocol_version if negotiation else None))
            return {"ok": True}

        mock_context.command_bus.send.side_effect = _spy_send
        result = BatchExecutor().execute(
            batch_id="b-stdio",
            calls=[_call_spec()],
            max_concurrency=2,
            global_timeout=30.0,
            fail_fast=False,
        )

        assert result.success is True
        assert seen == [(None, SUPPORTED_PROTOCOL_VERSION)]
        assert get_routing_headers() is None
        assert get_current_protocol_negotiation() is None
