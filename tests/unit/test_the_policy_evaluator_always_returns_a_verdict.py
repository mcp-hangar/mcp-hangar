"""A tool call that cannot be inspected still ends in a verdict, not a stack trace.

`_serialize_arguments` caught `(TypeError, ValueError)` and promised `None` "so
the caller can fail closed rather than crash". `RecursionError` is neither, so
nesting the JSON encoder could not walk -- about 992 levels, roughly 7 KB, well
under any `maxPayloadBytes` an operator would set -- propagated out of
`evaluate()` and out of `_enforce_l7_policy`, which calls it with no `try`.

That is not fail-closed, it is failure. In Enforce the call died with no
`EgressPolicyDeniedError`, no observation and no audit entry -- unattributed,
in a plane whose thesis is that every call ends in an attributable verdict. And
in Audit it aborted the call outright, which is precisely what ADR-013 promises
Audit does not do.

`maxPayloadBytes` could never have helped: the size check reads the string the
serializer returns, so the guard sits behind the thing that breaks.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest

from mcp_hangar.domain.events import EgressPolicyViolationObserved
from mcp_hangar.domain.exceptions import EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import (
    ArgumentRules,
    Decision,
    L7Policy,
    PolicyMode,
    ToolAction,
    ToolRules,
    evaluate,
)


def _nested(depth: int) -> dict[str, Any]:
    """`depth` levels of `{"a": {"a": ...}}` -- the shape the encoder recurses on."""
    root: dict[str, Any] = {}
    cursor = root
    for _ in range(depth):
        cursor["a"] = {}
        cursor = cursor["a"]
    return root


#: Past this, assume the encoder will not refuse and the test cannot run. Well
#: above 3.14's 34 710, with room for a future interpreter that recurses deeper.
_DEPTH_CEILING = 500_000


def _too_deep_to_serialize() -> dict[str, Any]:
    """The shallowest nesting this interpreter's JSON encoder refuses.

    Doubling rather than a fixed constant: the threshold moves by CPython
    version (see the module docstring), and a constant that stops reproducing
    the bug is worse than no test, because it still passes.
    """
    depth = 512
    while depth <= _DEPTH_CEILING:
        candidate = _nested(depth)
        try:
            json.dumps(candidate)
        except RecursionError:
            return candidate
        depth *= 2
    pytest.skip(f"this interpreter serializes {_DEPTH_CEILING} levels without recursing too deep")


class _Unstringable:
    """`json.dumps(default=str)` calls this, and it is somebody else's code."""

    def __str__(self) -> str:
        raise RuntimeError("this object refuses to be a string")


def _policy(mode: PolicyMode = PolicyMode.ENFORCE) -> L7Policy:
    return L7Policy(
        tools=ToolRules(allow=("some_tool",)),
        arguments=ArgumentRules(secret_patterns=("aws-keys",), max_payload_bytes=100_000),
        default_action=ToolAction.DENY,
        mode=mode,
    )


class _Proceeded(Exception):
    """Raised from a stubbed ensure_ready: the call got past the L7 gate."""


class TestEvaluateIsTotal:
    @pytest.mark.parametrize("depth", [10, 1_000, 100_000])  # spans every version's threshold
    @pytest.mark.parametrize("mode", [PolicyMode.ENFORCE, PolicyMode.AUDIT])
    def test_a_verdict_comes_back_at_any_nesting(self, depth: int, mode: PolicyMode) -> None:
        decision = evaluate("some_tool", _nested(depth), _policy(mode))

        assert isinstance(decision, Decision)

    def test_nesting_the_encoder_cannot_walk_is_denied_with_a_reason(self) -> None:
        decision = evaluate("some_tool", _too_deep_to_serialize(), _policy())

        assert decision.action is ToolAction.DENY
        assert any("could not be serialized" in reason for reason in decision.reasons)

    def test_shallow_arguments_are_unaffected(self) -> None:
        """The fix must not turn ordinary calls into denials."""
        decision = evaluate("some_tool", _nested(10), _policy())

        assert decision.action is ToolAction.ALLOW

    def test_an_object_whose_str_raises_is_a_verdict_too(self) -> None:
        """`default=str` is the second door onto the same problem."""
        decision = evaluate("some_tool", {"x": _Unstringable()}, _policy())

        assert decision.action is ToolAction.DENY


class TestTheCallerGetsTheVerdictItCanActOn:
    def test_enforce_blocks_with_an_attributable_error(self) -> None:
        server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=_policy())

        with pytest.raises(EgressPolicyDeniedError) as raised:
            server.invoke_tool("some_tool", _too_deep_to_serialize())

        assert "could not be serialized" in raised.value.reason

    def test_audit_observes_and_lets_the_call_through(self) -> None:
        """ADR-013's safe adoption path: Audit records, it does not block."""
        server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=_policy(PolicyMode.AUDIT))
        server.ensure_ready = Mock(side_effect=_Proceeded())  # type: ignore[method-assign]

        with pytest.raises(_Proceeded):
            server.invoke_tool("some_tool", _too_deep_to_serialize())

        observed = [e for e in server.collect_events() if isinstance(e, EgressPolicyViolationObserved)]
        assert len(observed) == 1
        assert observed[0].would_be_action == ToolAction.DENY.value
