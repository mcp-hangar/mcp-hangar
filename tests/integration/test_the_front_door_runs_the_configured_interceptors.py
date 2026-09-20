"""A front door's flat ``tools/call`` runs the configured interceptors, as ``hangar_call`` does (#1425).

The flat call dispatched through a ``BatchExecutor`` it built itself, which has
an empty interceptor pipeline. A ``payload_size`` validator therefore refused an
oversized argument on ``hangar_call`` and let the same argument through when the
tool was called by its flat name.

Driven over the real streamable-HTTP transport, through the app ``serve --http``
serves (``_front_door_harness``), with each caller authenticated by its API
key. The interceptors are configured the way ``load_configuration`` configures
them, from an ``interceptors:`` section. ``hangar_call`` is not on a front
door's surface, so the error it gives is taken from the same gateway served in
``egress``.

Naming: neutral placeholders only (store, write_item, tenant:a).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from mcp_hangar.server.config import _init_interceptors_from_config
from mcp_hangar.server.tools.batch import configure_interceptors
from tests.integration._front_door_harness import SERVER, TENANT_A, FrontDoor, front_door, jsonrpc

_TOOL = "write_item"
_CAP = 200
#: Its payload, ``{"name": ..., "arguments": ...}``, is over the cap.
_OVER = {"x": "a" * 500}
#: Its payload is under the cap.
_UNDER = {"x": "small"}


@pytest.fixture
def validator() -> Iterator[None]:
    """One ``payload_size`` validator, capped at ``_CAP`` bytes, configured from config."""
    _init_interceptors_from_config({"interceptors": {"validators": [{"type": "payload_size", "max_bytes": _CAP}]}})
    try:
        yield
    finally:
        configure_interceptors(None)


def _flat_error(door: FrontDoor, arguments: dict[str, Any]) -> str:
    """The error text of a flat call the gateway refused as a tool error."""
    payload = jsonrpc(door.call(TENANT_A, _TOOL, arguments))
    result = payload.get("result")
    assert isinstance(result, dict) and result.get("isError") is True, f"the flat call was not refused: {payload}"
    return str(result["content"][0]["text"])


def _hangar_call(door: FrontDoor, arguments: dict[str, Any]) -> dict[str, Any]:
    """The one call result of a ``hangar_call`` for ``_TOOL`` with *arguments*."""
    calls = [{"mcp_server": SERVER, "tool": _TOOL, "arguments": arguments}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    assert "result" in payload, payload
    (call,) = json.loads(payload["result"]["content"][0]["text"])["results"]
    return dict(call)


@pytest.mark.usefixtures("validator")
class TestTheConfiguredValidatorRunsOnTheFlatPath:
    def test_a_payload_over_the_cap_is_refused_with_the_hangar_call_error(self) -> None:
        with front_door((_TOOL,), topology="egress") as egress:
            refused = _hangar_call(egress, _OVER)
            egress_reached = list(egress.upstream.called)
        with front_door((_TOOL,)) as door:
            flat_error = _flat_error(door, _OVER)
            flat_reached = list(door.upstream.called)

        assert refused["success"] is False and refused["error_type"] == "ValidatorDenied", refused
        assert f"exceeds cap {_CAP}" in refused["error"], refused
        assert flat_error == refused["error"]
        assert egress_reached == [] and flat_reached == [], "a refused call reached the upstream"

    def test_a_payload_under_the_cap_is_served(self) -> None:
        with front_door((_TOOL,)) as door:
            result = door.result(TENANT_A, _TOOL, _UNDER)
            reached = list(door.upstream.called)

        assert result["content"][0]["text"] == f"did {_TOOL}"
        assert reached == [_TOOL]


def test_with_no_validator_configured_the_same_payload_is_served() -> None:
    """The control: the refusal above is the validator's, not the payload's."""
    configure_interceptors(None)
    with front_door((_TOOL,)) as door:
        result = door.result(TENANT_A, _TOOL, _OVER)

    assert result["content"][0]["text"] == f"did {_TOOL}"
