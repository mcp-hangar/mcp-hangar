"""Unit tests for mcp_hangar.application.mcp.tooling module.

Tests key functions, chain_validators, ToolErrorPayload, _default_error_mapper,
and mcp_tool_wrapper for both async and sync paths -- including error mapping,
on_error hooks, and the async approval gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.mcp.tooling import (
    ToolErrorPayload,
    _default_error_mapper,
    chain_validators,
    key_global,
    key_hangar_call,
    key_per_provider,
    mcp_tool_wrapper,
)


@dataclass
class FakeApprovalResult:
    """Minimal stand-in for enterprise ApprovalResult -- avoids enterprise import."""

    approved: bool
    error_code: str = ""
    approval_id: str = ""
    reason: str = ""


def _make_wrapper(**overrides: Any):
    defaults: dict[str, Any] = {
        "tool_name": "test_tool",
        "rate_limit_key": key_global,
        "check_rate_limit": MagicMock(),
    }
    defaults.update(overrides)
    return mcp_tool_wrapper(**defaults)


class TestKeyGlobal:
    """key_global returns 'global' regardless of positional or keyword args."""

    def test_no_args(self):
        assert key_global() == "global"

    def test_ignores_positional_args(self):
        assert key_global("a", "b", "c") == "global"

    def test_ignores_keyword_args(self):
        assert key_global(provider="acme", tool="call") == "global"


class TestKeyPerProvider:
    """key_per_provider returns 'provider:{name}' scoped to the first positional arg."""

    def test_basic(self):
        assert key_per_provider("acme") == "provider:acme"

    def test_ignores_extra_positional_args(self):
        assert key_per_provider("acme", "ignored", "also-ignored") == "provider:acme"

    def test_ignores_keyword_args(self):
        assert key_per_provider("beta", foo="bar") == "provider:beta"

    def test_different_provider_names(self):
        assert key_per_provider("delta") == "provider:delta"


class TestKeyHangarCall:
    """key_hangar_call returns 'hangar_call:{provider}' and ignores the tool name."""

    def test_basic(self):
        assert key_hangar_call("acme", "some_tool") == "hangar_call:acme"

    def test_ignores_tool_name(self):
        assert key_hangar_call("acme", "other_tool") == "hangar_call:acme"

    def test_different_provider(self):
        assert key_hangar_call("beta", "x") == "hangar_call:beta"


class TestToolErrorPayload:
    """ToolErrorPayload.to_dict() returns the expected stable structure."""

    def test_to_dict_structure(self):
        payload = ToolErrorPayload(error="bad", error_type="ValueError", details={"k": "v"})
        result = payload.to_dict()
        assert result == {"error": "bad", "type": "ValueError", "details": {"k": "v"}}

    def test_to_dict_empty_details(self):
        payload = ToolErrorPayload(error="oops", error_type="RuntimeError", details={})
        assert payload.to_dict()["details"] == {}

    def test_frozen_raises_on_mutation(self):
        payload = ToolErrorPayload(error="e", error_type="T", details={})
        with pytest.raises(AttributeError):
            payload.error = "modified"  # type: ignore[misc]


class TestDefaultErrorMapper:
    """_default_error_mapper produces a ToolErrorPayload from any exception."""

    def test_returns_tool_error_payload(self):
        assert isinstance(_default_error_mapper(ValueError("x")), ToolErrorPayload)

    def test_error_message_from_exception(self):
        result = _default_error_mapper(RuntimeError("boom"))
        assert result.error == "boom"

    def test_error_type_from_exception_class(self):
        result = _default_error_mapper(KeyError("missing"))
        assert result.error_type == "KeyError"

    def test_empty_message_falls_back_to_unknown(self):
        # str(Exception()) == '' which is falsy -- mapper substitutes "unknown error"
        result = _default_error_mapper(Exception())
        assert result.error == "unknown error"

    def test_details_always_empty_dict(self):
        result = _default_error_mapper(ValueError("x"))
        assert result.details == {}


class TestChainValidators:
    """chain_validators calls each validator in order; first exception stops chain."""

    def test_empty_chain_does_nothing(self):
        chain_validators()("arg1", key="val")

    def test_all_validators_called_in_order(self):
        calls: list[str] = []

        def v1(*args: Any, **kwargs: Any) -> None:
            calls.append("v1")

        def v2(*args: Any, **kwargs: Any) -> None:
            calls.append("v2")

        chain_validators(v1, v2)()
        assert calls == ["v1", "v2"]

    def test_first_exception_stops_chain(self):
        calls: list[str] = []

        def v1(*args: Any, **kwargs: Any) -> None:
            calls.append("v1")
            raise ValueError("bad input")

        def v2(*args: Any, **kwargs: Any) -> None:
            calls.append("v2")

        with pytest.raises(ValueError, match="bad input"):
            chain_validators(v1, v2)()

        assert calls == ["v1"]

    def test_passes_args_and_kwargs_through(self):
        received: dict[str, Any] = {}

        def v(a: str, b: str, *, c: str) -> None:
            received["a"] = a
            received["b"] = b
            received["c"] = c

        chain_validators(v)("x", "y", c="z")
        assert received == {"a": "x", "b": "y", "c": "z"}


class TestMcpToolWrapperAsyncNormal:
    """Async tool: normal execution returns the function result."""

    async def test_returns_function_result(self):
        async def tool() -> dict[str, int]:
            return {"data": 42}

        result = await _make_wrapper()(tool)()
        assert result == {"data": 42}

    async def test_rate_limit_key_called_with_args(self):
        rate_limit_key = MagicMock(return_value="global")
        check_rate_limit = MagicMock()

        async def tool(x: int, y: int) -> int:
            return x + y

        wrapped = mcp_tool_wrapper(
            tool_name="t",
            rate_limit_key=rate_limit_key,
            check_rate_limit=check_rate_limit,
        )(tool)

        await wrapped(1, 2)
        rate_limit_key.assert_called_once_with(1, 2)
        check_rate_limit.assert_called_once_with("global")

    async def test_validate_called_with_args(self):
        validate = MagicMock()

        async def tool(x: str) -> str:
            return x

        wrapped = mcp_tool_wrapper(
            tool_name="t",
            rate_limit_key=key_global,
            check_rate_limit=MagicMock(),
            validate=validate,
        )(tool)

        await wrapped("hello")
        validate.assert_called_once_with("hello")


class TestMcpToolWrapperAsyncException:
    """Async tool: exceptions inside the function are caught and mapped to error dicts."""

    async def test_exception_returns_error_dict(self):
        async def tool() -> None:
            raise ValueError("bad thing")

        result = await _make_wrapper()(tool)()
        assert result["error"] == "bad thing"
        assert result["type"] == "ValueError"
        assert "details" in result

    async def test_on_error_hook_called_with_exc_and_context(self):
        on_error = MagicMock()

        async def tool() -> None:
            raise RuntimeError("crash")

        await _make_wrapper(on_error=on_error)(tool)()
        on_error.assert_called_once()
        exc_arg, ctx_arg = on_error.call_args[0]
        assert isinstance(exc_arg, RuntimeError)
        assert ctx_arg["tool"] == "test_tool"

    async def test_on_error_hook_failure_is_swallowed(self):
        def bad_hook(exc: Exception, ctx: dict[str, Any]) -> None:
            raise RuntimeError("hook itself exploded")

        async def tool() -> None:
            raise ValueError("original")

        result = await _make_wrapper(on_error=bad_hook)(tool)()
        assert "error" in result

    async def test_rate_limit_exception_propagates(self):
        class RateLimitExceeded(Exception):
            pass

        def check_rate_limit(key: str) -> None:
            raise RateLimitExceeded("too fast")

        async def tool() -> str:
            return "ok"

        wrapped = mcp_tool_wrapper(
            tool_name="t",
            rate_limit_key=key_global,
            check_rate_limit=check_rate_limit,
        )(tool)

        with pytest.raises(RateLimitExceeded):
            await wrapped()

    async def test_validate_exception_propagates(self):
        def bad_validate(*args: Any, **kwargs: Any) -> None:
            raise ValueError("invalid input")

        async def tool() -> str:
            return "ok"

        with pytest.raises(ValueError, match="invalid input"):
            await _make_wrapper(validate=bad_validate)(tool)()


class TestMcpToolWrapperAsyncApproval:
    """Async tool: check_approval gate approved / denied behavior."""

    async def test_approval_approved_continues_to_execution(self):
        async def approval(*args: Any, **kwargs: Any) -> FakeApprovalResult:
            return FakeApprovalResult(approved=True)

        async def tool() -> dict[str, str]:
            return {"result": "done"}

        result = await _make_wrapper(check_approval=approval)(tool)()
        assert result == {"result": "done"}

    async def test_approval_denied_returns_error_dict_with_approval_id(self):
        async def approval(*args: Any, **kwargs: Any) -> FakeApprovalResult:
            return FakeApprovalResult(
                approved=False,
                error_code="approval_denied",
                approval_id="req-123",
                reason="Manager rejected",
            )

        async def tool() -> dict[str, str]:
            return {"result": "should not reach"}

        result = await _make_wrapper(check_approval=approval)(tool)()
        assert result["error"] == "approval_denied"
        assert result["approval_id"] == "req-123"
        assert result["message"] == "Manager rejected"

    async def test_no_approval_check_executes_directly(self):
        call_count = {"n": 0}

        async def tool() -> str:
            call_count["n"] += 1
            return "ok"

        await _make_wrapper(check_approval=None)(tool)()
        assert call_count["n"] == 1


class TestMcpToolWrapperSyncNormal:
    """Sync tool: normal execution returns the function result."""

    def test_returns_function_result(self):
        def tool() -> dict[str, int]:
            return {"data": 99}

        result = _make_wrapper()(tool)()
        assert result == {"data": 99}

    def test_rate_limit_called_before_tool_execution(self):
        execution_order: list[str] = []
        check_rl = MagicMock(side_effect=lambda k: execution_order.append("rate_limit"))

        def tool() -> str:
            execution_order.append("tool")
            return "ok"

        wrapped = mcp_tool_wrapper(
            tool_name="t",
            rate_limit_key=key_global,
            check_rate_limit=check_rl,
        )(tool)

        wrapped()
        assert execution_order == ["rate_limit", "tool"]


class TestMcpToolWrapperSyncException:
    """Sync tool: exceptions inside the function are caught and mapped to error dicts."""

    def test_exception_returns_error_dict(self):
        def tool() -> None:
            raise KeyError("missing_key")

        result = _make_wrapper()(tool)()
        assert result["type"] == "KeyError"
        assert "details" in result

    def test_on_error_hook_called_with_exc_and_context(self):
        on_error = MagicMock()

        def tool() -> None:
            raise ValueError("sync crash")

        _make_wrapper(on_error=on_error)(tool)()
        on_error.assert_called_once()
        exc_arg, ctx_arg = on_error.call_args[0]
        assert isinstance(exc_arg, ValueError)
        assert ctx_arg["tool"] == "test_tool"

    def test_on_error_hook_failure_is_swallowed(self):
        def bad_hook(exc: Exception, ctx: dict[str, Any]) -> None:
            raise ValueError("hook exploded")

        def tool() -> None:
            raise RuntimeError("original error")

        result = _make_wrapper(on_error=bad_hook)(tool)()
        assert "error" in result

    def test_rate_limit_exception_propagates(self):
        class RateLimitExceeded(Exception):
            pass

        def check_rate_limit(key: str) -> None:
            raise RateLimitExceeded("sync too fast")

        def tool() -> str:
            return "ok"

        wrapped = mcp_tool_wrapper(
            tool_name="t",
            rate_limit_key=key_global,
            check_rate_limit=check_rate_limit,
        )(tool)

        with pytest.raises(RateLimitExceeded):
            wrapped()

    def test_sync_path_does_not_invoke_check_approval(self):
        approval_calls: list[bool] = []

        async def approval(*args: Any, **kwargs: Any) -> FakeApprovalResult:
            approval_calls.append(True)
            return FakeApprovalResult(approved=False)

        def tool() -> str:
            return "sync result"

        wrapped = mcp_tool_wrapper(
            tool_name="t",
            rate_limit_key=key_global,
            check_rate_limit=MagicMock(),
            check_approval=approval,
        )(tool)

        result = wrapped()
        assert result == "sync result"
        assert approval_calls == []
