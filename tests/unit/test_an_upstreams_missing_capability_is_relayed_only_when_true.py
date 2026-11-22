"""The rule that decides whether a front-door caller is shown an upstream's ``-32021`` (#2315).

Relayed only when every capability the upstream names is one the caller left
out of its declaration; anything else, or anything unreadable, is None (the
tool error). The served path is covered over the transport in
``tests/integration/test_an_upstreams_missing_capability_reaches_the_caller.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp_types import ClientCapabilities

from mcp_hangar.domain.model.mcp_server import _missing_capability_error
from mcp_hangar.fastmcp_server.missing_capability_relay import relayed_missing_capability


def _ctx(declared: dict[str, Any] | None, version: str = "2026-07-28") -> Any:
    capabilities = None if declared is None else ClientCapabilities.model_validate(declared)
    return SimpleNamespace(session=SimpleNamespace(protocol_version=version, client_capabilities=capabilities))


def _upstream(required: Any) -> dict[str, Any]:
    return {"code": -32021, "message": "missing", "data": {"requiredCapabilities": required}}


@pytest.mark.parametrize(
    ("declared", "required"),
    [
        ({}, {"sampling": {}}),
        ({"sampling": {}}, {"elicitation": {}}),
        ({"extensions": {"x.example/a": {}}}, {"extensions": {"x.example/b": {}}}),
        ({}, {"experimental": {"x.example/flag": {}}}),
    ],
)
def test_a_capability_the_caller_left_out_is_relayed(declared: dict[str, Any], required: dict[str, Any]) -> None:
    error = relayed_missing_capability(_upstream(required), _ctx(declared))

    assert error is not None
    assert error.error.code == -32021  # type: ignore[attr-defined]
    assert error.error.data == {"requiredCapabilities": required}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("declared", "required"),
    [
        # Declared in another form still counts as declared.
        ({"elicitation": {"form": {}}}, {"elicitation": {"url": {}}}),
        ({"sampling": {}}, {"sampling": {}, "roots": {}}),
        ({"experimental": {"x.example/flag": {}}}, {"experimental": {"x.example/flag": {}}}),
        # Hangar's own namespace, at either level.
        ({}, {"extensions": {"io.mcp-hangar/approval": {}}}),
        ({}, {"io.mcp-hangar/approval": {}}),
        # A family the SDK does not model: dropped from any declaration, so it cannot be judged.
        ({"x.example/future": {}}, {"x.example/future": {}}),
        ({}, {"x.example/future": {}}),
        # Not a ClientCapabilities object.
        ({}, {}),
        ({}, {"sampling": True}),
        ({}, {"extensions": {}}),
        ({}, {"extensions": {"x.example/a": True}}),
        ({}, ["sampling"]),
    ],
)
def test_anything_else_is_the_tool_error(declared: dict[str, Any], required: Any) -> None:
    assert relayed_missing_capability(_upstream(required), _ctx(declared)) is None


def test_a_legacy_caller_or_an_unreadable_declaration_is_the_tool_error() -> None:
    upstream = _upstream({"sampling": {}})

    assert relayed_missing_capability(upstream, _ctx({}, version="2025-11-25")) is None
    assert relayed_missing_capability(upstream, _ctx(None)) is None
    assert relayed_missing_capability(upstream, SimpleNamespace()) is None
    assert relayed_missing_capability(None, _ctx({})) is None


def test_the_domain_keeps_only_the_fields_the_spec_gives_the_error() -> None:
    error = {
        "code": -32021,
        "message": "m" * 2000,
        "data": {"requiredCapabilities": {"sampling": {}}, "trace": "internal"},
        "extra": 1,
    }

    kept = _missing_capability_error(error)

    assert kept == {"code": -32021, "message": "m" * 500, "data": {"requiredCapabilities": {"sampling": {}}}}
    assert _missing_capability_error({"code": -32602, "message": "x"}) is None
    assert _missing_capability_error({"code": -32021, "message": "x"}) is None
    assert _missing_capability_error({"code": -32021, "data": {"requiredCapabilities": {}}}) is None
