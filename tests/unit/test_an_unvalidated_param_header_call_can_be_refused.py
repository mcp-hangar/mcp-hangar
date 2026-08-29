"""`headers.param_validation.required` refuses what it cannot validate (#1053, ADR-025).

Refusing to *match* a header selector is the default and needs no flag. This is
the second, opt-in control on top: refuse the call outright rather than serve it
with `Mcp-Param-*` headers nobody compared against the body.

It is deliberately opt-in and deliberately weaker as a default, because its
blast radius is different: it turns an upstream listing failure into a
client-visible refusal for every call carrying header parameters, whether or not
any policy selects on them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar.context import PARAM_VALIDATION_STATE_ATTR
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.server.config import _init_param_validation_from_config
from mcp_hangar.server.config_schema import validate_config
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.tasks_wire import HEADER_MISMATCH

MODERN = "2026-07-28"


def _ctx(*, skipped: bool) -> SimpleNamespace:
    principal = Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER, tenant_id="tenant:a")
    state = SimpleNamespace(auth=SimpleNamespace(principal=principal))
    if skipped:
        setattr(state, PARAM_VALIDATION_STATE_ATTR, True)
    body = json.dumps(
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "add", "arguments": {"a": 1}}}
    ).encode()
    request = SimpleNamespace(
        state=state,
        _body=body,
        headers={"mcp-param-region": "eu-west-1", "mcp-protocol-version": MODERN},
    )
    return SimpleNamespace(request=request)


def _handlers() -> dict[str, Any]:
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers


def _call(ctx: SimpleNamespace) -> Any:
    """Drive the front door's tools/call handler, returning whatever it raised."""
    handlers = _handlers()
    captured: list[BaseException] = []

    async def _run() -> None:
        try:
            await handlers["tools/call"](ctx, SimpleNamespace(name="add", arguments={"a": 1}))
        except BaseException as exc:  # noqa: BLE001 -- the verdict is the subject of the test
            captured.append(exc)

    anyio.run(_run)
    return captured[0] if captured else None


@pytest.fixture()
def empty_projection(monkeypatch):
    """An empty flat map: an unrefused call ends in -32601, which is not -32020."""
    monkeypatch.setattr(flat_tool_projection, "_build_flat_map", lambda _tenant: {})
    monkeypatch.setattr(
        "mcp_hangar.server.tools.tool_permissions.management_tools_for",
        lambda _ctx: set(),
    )


@pytest.fixture(autouse=True)
def default_off():
    """Restore the process default around every test."""
    before = flat_tool_projection.param_validation_required()
    yield
    flat_tool_projection.set_param_validation_required(before)


def _code(error: Any) -> int | None:
    return getattr(error, "code", None) or getattr(getattr(error, "error", None), "code", None)


class TestTheDefault:
    def test_an_unvalidated_call_is_served_by_default(self, empty_projection) -> None:
        """The default is Decision 1 alone: the selector does not match, and the
        call is not refused for that reason."""
        error = _call(_ctx(skipped=True))

        assert _code(error) != HEADER_MISMATCH

    def test_the_flag_is_off_when_the_config_says_nothing(self) -> None:
        flat_tool_projection.set_param_validation_required(True)

        _init_param_validation_from_config({})

        assert flat_tool_projection.param_validation_required() is False


class TestTheRefusal:
    def test_an_unvalidated_call_is_refused(self, empty_projection) -> None:
        flat_tool_projection.set_param_validation_required(True)

        error = _call(_ctx(skipped=True))

        assert _code(error) == HEADER_MISMATCH
        assert "could not be validated" in str(error)

    def test_a_validated_call_is_untouched(self, empty_projection) -> None:
        """Only the request whose validation was skipped is refused: an operator
        turning this on does not lose every call carrying header parameters."""
        flat_tool_projection.set_param_validation_required(True)

        error = _call(_ctx(skipped=False))

        assert _code(error) != HEADER_MISMATCH


class TestTheConfigSurface:
    def test_the_flag_is_read_off_the_config_file(self) -> None:
        _init_param_validation_from_config({"headers": {"param_validation": {"required": True}}})

        assert flat_tool_projection.param_validation_required() is True

    def test_switching_it_back_off_takes_effect(self) -> None:
        _init_param_validation_from_config({"headers": {"param_validation": {"required": True}}})
        _init_param_validation_from_config({"headers": {"param_validation": {"required": False}}})

        assert flat_tool_projection.param_validation_required() is False

    def test_a_non_boolean_refuses_to_start(self) -> None:
        """Quietly resolving `required: "yes"` to off hands an operator the
        permissive behaviour while their config file says otherwise."""
        with pytest.raises(ConfigurationError, match="must be a boolean"):
            _init_param_validation_from_config({"headers": {"param_validation": {"required": "yes"}}})

    def test_the_section_is_known_to_the_schema(self) -> None:
        """A section nothing reads is the failure `validate_config` exists to catch."""
        assert validate_config({"headers": {"param_validation": {"required": True}}}) == []
        assert validate_config({"headers": {"param_validationn": {}}}) != []
