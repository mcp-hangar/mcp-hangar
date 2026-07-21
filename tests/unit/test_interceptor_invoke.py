"""Unit tests for the PR #2624 interceptor reconciliation.

Covers ``interceptor/invoke``, hook objects carrying ``events`` + ``phase``,
phase-aware (request/response) delivery, and capability-negotiation gating.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_hangar._sdk_compat import FastMCP

from mcp_hangar.domain.events import InterceptorInvoked
from mcp_hangar.domain.value_objects.hook import Hook, HookPhase
from mcp_hangar.fastmcp_server.interceptors_list import (
    INTERCEPTOR_EXT_HEADER,
    INTERCEPTOR_EXT_VALUE,
    SEP_2624_PIN,
    SEP_2624_PR,
    interceptor_invoke_handler,
    interceptor_invoke_result,
    interceptors_list_handler,
    interceptors_list_response_v2,
    register_interceptors_list,
)
from mcp_hangar.infrastructure.event_bus import get_event_bus

_NEG = {INTERCEPTOR_EXT_HEADER: INTERCEPTOR_EXT_VALUE}


class _Recorder:
    def __init__(self) -> None:
        self.hooks: list[Hook] = []

    def on_hook(self, hook: Hook) -> None:
        self.hooks.append(hook)


@pytest.fixture()
def bus_recorder():
    bus = get_event_bus()
    rec = _Recorder()
    bus.subscribe_hooks(rec)
    try:
        yield rec
    finally:
        bus.unsubscribe_hooks(rec)


class TestPinnedRevision:
    def test_pin_recorded(self):
        assert SEP_2624_PR == 2624
        # SEP prose head SHA (PR #2624) -- unchanged; only the schema pin moved.
        assert SEP_2624_PIN == "8029c78ae88a3aadeb83c2f63cbbf2f04ec43e3a"

    def test_capability_key_is_sep2133_reverse_dns(self):
        # experimental-ext-interceptors #25 (99bc7c9) realigned the capability
        # key to the SEP-2133 extensions format; our gate value mirrors it.
        assert INTERCEPTOR_EXT_VALUE == "io.modelcontextprotocol/interceptors"


class TestInterceptorInvokeResult:
    def test_validation_result_shape(self, bus_recorder):
        result = interceptor_invoke_result(
            {"name": "mcp-hangar-validator", "event": "tools/call", "phase": "request", "payload": {}}
        )
        assert result["interceptor"] == "mcp-hangar-validator"
        assert result["type"] == "validation"
        assert result["phase"] == "request"
        assert result["valid"] is True

    def test_mutation_result_is_passthrough(self, bus_recorder):
        payload = {"method": "tools/call", "params": {"name": "x"}}
        result = interceptor_invoke_result(
            {"name": "mcp-hangar-mutator", "event": "tools/call", "phase": "response", "payload": payload}
        )
        assert result["type"] == "mutation"
        assert result["phase"] == "response"
        assert result["modified"] is False
        assert result["payload"] == payload

    def test_unknown_interceptor_rejected(self, bus_recorder):
        with pytest.raises(ValueError, match="unknown interceptor"):
            interceptor_invoke_result({"name": "nope", "event": "tools/call", "phase": "request"})

    def test_unsupported_event_rejected(self, bus_recorder):
        with pytest.raises(ValueError, match="does not hook event"):
            interceptor_invoke_result({"name": "mcp-hangar-mutator", "event": "tools/list", "phase": "request"})

    def test_bad_phase_rejected(self, bus_recorder):
        with pytest.raises(ValueError, match="phase must be"):
            interceptor_invoke_result({"name": "mcp-hangar-validator", "event": "tools/call", "phase": "sideways"})


class TestPhaseAwareDelivery:
    def test_hook_fires_on_request(self, bus_recorder):
        interceptor_invoke_result({"name": "mcp-hangar-validator", "event": "tools/call", "phase": "request"})
        assert len(bus_recorder.hooks) == 1
        hook = bus_recorder.hooks[0]
        assert hook.phase is HookPhase.REQUEST
        assert isinstance(hook.event, InterceptorInvoked)
        assert hook.event.lifecycle_event == "tools/call"
        assert hook.event.phase == "request"

    def test_hook_fires_on_response(self, bus_recorder):
        interceptor_invoke_result({"name": "mcp-hangar-mutator", "event": "tools/call", "phase": "response"})
        assert bus_recorder.hooks[-1].phase is HookPhase.RESPONSE

    def test_both_legs_deliver_distinct_phases(self, bus_recorder):
        interceptor_invoke_result({"name": "mcp-hangar-validator", "event": "tools/call", "phase": "request"})
        interceptor_invoke_result({"name": "mcp-hangar-validator", "event": "tools/call", "phase": "response"})
        phases = [h.phase for h in bus_recorder.hooks]
        assert HookPhase.REQUEST in phases
        assert HookPhase.RESPONSE in phases


class TestListV2Shape:
    def test_v2_entries_carry_events_and_phase(self):
        data = interceptors_list_response_v2()
        for entry in data["interceptors"]:
            assert entry["type"] in ("validation", "mutation")
            assert "hooks" in entry
            for hook in entry["hooks"]:
                assert isinstance(hook["events"], list) and hook["events"]
                assert hook["phase"] in ("request", "response")

    def test_v2_validator_hooks(self):
        validator = interceptors_list_response_v2()["interceptors"][0]
        assert validator["name"] == "mcp-hangar-validator"
        assert validator["hooks"] == [{"events": ["tools/call", "tools/list"], "phase": "request"}]


def _list_client() -> TestClient:
    app = Starlette(routes=[Route("/interceptors/list", interceptors_list_handler, methods=["GET"])])
    return TestClient(app)


def _invoke_client() -> TestClient:
    app = Starlette(routes=[Route("/interceptor/invoke", interceptor_invoke_handler, methods=["POST"])])
    return TestClient(app)


class TestCapabilityNegotiationGating:
    def test_list_default_is_legacy_shape(self):
        resp = _list_client().get("/interceptors/list")
        assert resp.status_code == 200
        entry = resp.json()["interceptors"][0]
        # Legacy default: flat supportedEvents, no hooks array.
        assert entry["type"] == "validator"
        assert "supportedEvents" in entry
        assert "hooks" not in entry

    def test_list_negotiated_is_v2_shape(self):
        resp = _list_client().get("/interceptors/list", headers=_NEG)
        entry = resp.json()["interceptors"][0]
        assert entry["type"] == "validation"
        assert "hooks" in entry

    def test_list_negotiated_via_query_param(self):
        resp = _list_client().get(f"/interceptors/list?ext={INTERCEPTOR_EXT_VALUE}")
        assert resp.json()["interceptors"][0]["type"] == "validation"

    def test_invoke_not_exposed_without_negotiation(self, bus_recorder):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "interceptor/invoke",
            "params": {"name": "mcp-hangar-validator", "event": "tools/call", "phase": "request"},
        }
        resp = _invoke_client().post("/interceptor/invoke", json=body)
        assert resp.status_code == 404
        # No hook delivered when the extension is not negotiated.
        assert bus_recorder.hooks == []

    def test_invoke_works_when_negotiated(self, bus_recorder):
        body = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "interceptor/invoke",
            "params": {"name": "mcp-hangar-validator", "event": "tools/call", "phase": "request"},
        }
        resp = _invoke_client().post("/interceptor/invoke", json=body, headers=_NEG)
        assert resp.status_code == 200
        env = resp.json()
        assert env["id"] == 7
        assert env["result"]["valid"] is True
        assert len(bus_recorder.hooks) == 1

    def test_invoke_bad_params_returns_jsonrpc_error(self):
        body = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "interceptor/invoke",
            "params": {"name": "ghost", "event": "tools/call", "phase": "request"},
        }
        resp = _invoke_client().post("/interceptor/invoke", json=body, headers=_NEG)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32602

    def test_invoke_wrong_method_returns_method_not_found(self):
        body = {"jsonrpc": "2.0", "id": 3, "method": "interceptors/list", "params": {}}
        resp = _invoke_client().post("/interceptor/invoke", json=body, headers=_NEG)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == -32601


class TestRegisterBothRoutes:
    def test_both_routes_registered_and_served(self):
        mcp = FastMCP("test-interceptors")
        register_interceptors_list(mcp)
        client = TestClient(mcp.streamable_http_app())

        # invoke hidden without negotiation
        assert client.post("/interceptor/invoke", json={}).status_code == 404
        # invoke served when negotiated
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "interceptor/invoke",
            "params": {"name": "mcp-hangar-mutator", "event": "tools/call", "phase": "request"},
        }
        resp = client.post("/interceptor/invoke", json=body, headers=_NEG)
        assert resp.status_code == 200
        assert resp.json()["result"]["type"] == "mutation"
