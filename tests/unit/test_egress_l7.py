"""Tests for the L7 egress policy engine (domain.policies.egress_l7)."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.policies.egress_l7 import (
    ArgumentRules,
    evaluate,
    evaluate_tool,
    L7Policy,
    PolicyMode,
    scan_arguments,
    ToolAction,
    ToolRules,
)

# Sample secrets that match the reused redactor value-regexes.
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
JWT = "eyJ" + "a" * 60 + "." + "b" * 60
PEM = "-----BEGIN OPENSSH PRIVATE KEY-----"
GITHUB_PAT = "ghp_" + "a" * 36


# --- tool matching ---------------------------------------------------------


def test_evaluate_tool_precedence_deny_wins() -> None:
    rules = ToolRules(allow=("*",), deny=("delete_*",), require_approval=("delete_*",))
    action, _ = evaluate_tool("delete_repo", rules, ToolAction.DENY)
    assert action is ToolAction.DENY


def test_evaluate_tool_require_approval_over_allow() -> None:
    rules = ToolRules(allow=("*",), require_approval=("create_*",))
    action, _ = evaluate_tool("create_issue", rules, ToolAction.DENY)
    assert action is ToolAction.REQUIRE_APPROVAL


def test_evaluate_tool_allow_glob() -> None:
    rules = ToolRules(allow=("get_*", "list_*"))
    assert evaluate_tool("get_user", rules, ToolAction.DENY)[0] is ToolAction.ALLOW
    assert evaluate_tool("list_repos", rules, ToolAction.DENY)[0] is ToolAction.ALLOW


def test_evaluate_tool_falls_to_default() -> None:
    rules = ToolRules(allow=("get_*",))
    assert evaluate_tool("write_file", rules, ToolAction.DENY)[0] is ToolAction.DENY
    assert evaluate_tool("write_file", rules, ToolAction.ALLOW)[0] is ToolAction.ALLOW


def test_evaluate_tool_glob_is_case_sensitive() -> None:
    rules = ToolRules(allow=("get_*",))
    # Deterministic: uppercase does not match a lowercase glob.
    assert evaluate_tool("GET_user", rules, ToolAction.DENY)[0] is ToolAction.DENY


# --- argument scanning -----------------------------------------------------


def test_scan_arguments_detects_secrets() -> None:
    rules = ArgumentRules(secret_patterns=("aws-keys", "jwt", "pem-blocks"))
    assert scan_arguments({"key": AWS_KEY}, rules)
    assert scan_arguments({"token": JWT}, rules)
    assert scan_arguments({"pem": PEM}, rules)


def test_scan_arguments_only_configured_groups() -> None:
    # A GitHub PAT is present but only aws-keys is configured -> no violation.
    rules = ArgumentRules(secret_patterns=("aws-keys",))
    assert scan_arguments({"token": GITHUB_PAT}, rules) == []
    assert scan_arguments({"token": AWS_KEY}, rules)


def test_scan_arguments_unknown_group_ignored() -> None:
    rules = ArgumentRules(secret_patterns=("not-a-real-group",))
    assert scan_arguments({"token": AWS_KEY}, rules) == []


def test_scan_arguments_max_payload_bytes() -> None:
    rules = ArgumentRules(max_payload_bytes=16)
    assert scan_arguments({"data": "x" * 100}, rules)
    assert scan_arguments("tiny", rules) == []


def test_scan_arguments_clean() -> None:
    rules = ArgumentRules(secret_patterns=("aws-keys", "jwt"), max_payload_bytes=1024)
    assert scan_arguments({"query": "list all issues"}, rules) == []


def test_scan_arguments_nested_dict() -> None:
    rules = ArgumentRules(secret_patterns=("aws-keys",))
    assert scan_arguments({"outer": {"inner": AWS_KEY}}, rules)


def test_scan_arguments_unserializable_fails_closed() -> None:
    circular: dict[str, object] = {}
    circular["self"] = circular
    violations = scan_arguments(circular, ArgumentRules(max_payload_bytes=10))
    assert violations and "serialized" in violations[0]


def test_scan_arguments_no_rules_skips_serialization() -> None:
    # An unserializable payload with no rules must not crash -- and must be clean.
    circular: dict[str, object] = {}
    circular["self"] = circular
    assert scan_arguments(circular, ArgumentRules()) == []


# --- full evaluation -------------------------------------------------------


def test_evaluate_allowed_tool_clean_args() -> None:
    policy = L7Policy(
        tools=ToolRules(allow=("get_*",)),
        arguments=ArgumentRules(secret_patterns=("aws-keys",)),
    )
    decision = evaluate("get_user", {"id": 1}, policy)
    assert decision.action is ToolAction.ALLOW


def test_evaluate_secret_in_args_denies_allowed_tool() -> None:
    policy = L7Policy(
        tools=ToolRules(allow=("*",)),
        arguments=ArgumentRules(secret_patterns=("aws-keys",)),
    )
    decision = evaluate("get_user", {"key": AWS_KEY}, policy)
    assert decision.action is ToolAction.DENY
    assert any("aws-keys" in r for r in decision.reasons)


def test_evaluate_require_approval_preserved_when_clean() -> None:
    policy = L7Policy(tools=ToolRules(require_approval=("create_*",)))
    decision = evaluate("create_issue", {"title": "bug"}, policy)
    assert decision.action is ToolAction.REQUIRE_APPROVAL


def test_evaluate_denied_tool_short_circuits() -> None:
    policy = L7Policy(
        tools=ToolRules(deny=("delete_*",)),
        arguments=ArgumentRules(max_payload_bytes=1),
    )
    decision = evaluate("delete_repo", {"big": "x" * 100}, policy)
    assert decision.action is ToolAction.DENY
    # Deny reason is the tool rule, not the argument scan (short-circuited).
    assert any("deny rule" in r for r in decision.reasons)


def test_evaluate_default_deny() -> None:
    policy = L7Policy(tools=ToolRules(allow=("get_*",)), default_action=ToolAction.DENY)
    assert evaluate("write_file", {}, policy).action is ToolAction.DENY


# --- from_dict (wire form) -------------------------------------------------


def test_from_dict_full() -> None:
    p = L7Policy.from_dict(
        {
            "tools": {"allow": ["get_*"], "deny": ["delete_*"], "requireApproval": ["create_*"]},
            "arguments": {"secretPatterns": ["aws-keys", "jwt"], "maxPayloadBytes": 1024},
            "defaultAction": "Deny",
        }
    )
    assert p.tools.allow == ("get_*",)
    assert p.tools.deny == ("delete_*",)
    assert p.tools.require_approval == ("create_*",)
    assert p.arguments.secret_patterns == ("aws-keys", "jwt")
    assert p.arguments.max_payload_bytes == 1024
    assert p.default_action is ToolAction.DENY


def test_from_dict_defaults_and_case() -> None:
    p = L7Policy.from_dict({"defaultAction": "allow"})
    assert p.tools.allow == () and p.tools.deny == ()
    assert p.arguments.secret_patterns == () and p.arguments.max_payload_bytes is None
    assert p.default_action is ToolAction.ALLOW
    # defaultAction defaults to Deny when omitted.
    assert L7Policy.from_dict({}).default_action is ToolAction.DENY


def test_from_dict_roundtrips_through_evaluate() -> None:
    p = L7Policy.from_dict({"tools": {"deny": ["delete_*"]}, "defaultAction": "Allow"})
    assert evaluate("delete_repo", {}, p).action is ToolAction.DENY
    assert evaluate("get_user", {}, p).action is ToolAction.ALLOW


# --- mode (Audit | Enforce) ------------------------------------------------


def test_policy_default_mode_is_enforce() -> None:
    # A programmatically-built policy with no mode blocks (fail-closed / safe).
    assert L7Policy().mode is PolicyMode.ENFORCE


def test_from_dict_parses_audit_mode() -> None:
    p = L7Policy.from_dict({"tools": {"deny": ["delete_*"]}, "mode": "Audit"})
    assert p.mode is PolicyMode.AUDIT


def test_from_dict_parses_enforce_mode() -> None:
    p = L7Policy.from_dict({"tools": {"deny": ["delete_*"]}, "mode": "Enforce"})
    assert p.mode is PolicyMode.ENFORCE


def test_from_dict_mode_absent_defaults_enforce() -> None:
    # Backward-compat: a mode-less payload from an older operator keeps blocking.
    assert L7Policy.from_dict({}).mode is PolicyMode.ENFORCE


@pytest.mark.parametrize("bad_mode", ["audit", "ENFORCE", "observe", "", None, 1, True])
def test_from_dict_unrecognized_mode_defaults_enforce(bad_mode: object) -> None:
    # Anything that is not exactly "Audit" fails closed to Enforce.
    assert L7Policy.from_dict({"mode": bad_mode}).mode is PolicyMode.ENFORCE


def test_mode_does_not_affect_evaluate_verdict() -> None:
    # evaluate() stays pure: the verdict is identical regardless of mode.
    deny_rule = ToolRules(deny=("delete_*",))
    audit = L7Policy(tools=deny_rule, mode=PolicyMode.AUDIT)
    enforce = L7Policy(tools=deny_rule, mode=PolicyMode.ENFORCE)
    assert evaluate("delete_repo", {}, audit).action is ToolAction.DENY
    assert evaluate("delete_repo", {}, enforce).action is ToolAction.DENY


@pytest.mark.parametrize(
    "bad",
    [
        {"defaultAction": "maybe"},
        {"tools": {"allow": "get_*"}},  # not a list
        {"tools": {"deny": [1, 2]}},  # not strings
        {"arguments": {"maxPayloadBytes": -5}},
        {"arguments": {"maxPayloadBytes": "big"}},
        {"arguments": {"secretPatterns": "aws-keys"}},
        "not-an-object",
    ],
)
def test_from_dict_rejects_malformed(bad: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        L7Policy.from_dict(bad)  # type: ignore[arg-type]
