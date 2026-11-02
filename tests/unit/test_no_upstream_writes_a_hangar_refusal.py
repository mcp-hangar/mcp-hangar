"""Hangar's refusal keys never come from an upstream (ADR-029 s12.3, #1918)."""

from __future__ import annotations

from mcp_hangar.domain.value_objects.refusal import (
    AUTHORIZATION_DENIAL_KEY,
    TOOL_FAILURE_KEY,
    without_refusal_keys,
)


def test_both_keys_go_and_every_other_key_stays() -> None:
    result = {
        "content": [{"type": "text", "text": "x"}],
        "isError": True,
        "_meta": {TOOL_FAILURE_KEY: {}, AUTHORIZATION_DENIAL_KEY: {}, "io.modelcontextprotocol/toolFailure": {"a": 1}},
    }
    assert without_refusal_keys(result) == {
        "content": [{"type": "text", "text": "x"}],
        "isError": True,
        "_meta": {"io.modelcontextprotocol/toolFailure": {"a": 1}},
    }


def test_a_meta_left_empty_is_not_sent() -> None:
    assert without_refusal_keys({"_meta": {TOOL_FAILURE_KEY: {}}, "content": []}) == {"content": []}


def test_a_result_without_the_keys_is_the_same_object() -> None:
    plain = {"content": [], "_meta": {"other": 1}}
    assert without_refusal_keys(plain) is plain
    not_a_dict = {"_meta": "text"}
    assert without_refusal_keys(not_a_dict) is not_a_dict
