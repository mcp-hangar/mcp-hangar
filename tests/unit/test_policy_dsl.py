"""Tests for the MCP Policy DSL parser/validator (v1 grammar, ADR-006)."""

import pytest

from mcp_hangar.domain.policies import HookRule, PolicyDSL, parse_policy


def test_parse_tcp_connect_hook() -> None:
    policy = parse_policy(
        {
            "name": "egress",
            "hooks": [
                {
                    "hook": "tcp_connect",
                    "action": "block",
                    "match": {"remote_host": "10.0.0.0/8", "remote_port": 443},
                }
            ],
        }
    )
    assert isinstance(policy, PolicyDSL)
    assert policy.name == "egress"
    assert len(policy.hooks) == 1
    rule = policy.hooks[0]
    assert isinstance(rule, HookRule)
    assert rule.hook == "tcp_connect"
    assert rule.action == "block"
    assert dict(rule.match) == {"remote_host": "10.0.0.0/8", "remote_port": 443}


def test_parse_sk_alloc_hook_without_match() -> None:
    policy = parse_policy(
        {
            "name": "socket-audit",
            "hooks": [{"hook": "sk_alloc", "action": "alert"}],
        }
    )
    rule = policy.hooks[0]
    assert rule.hook == "sk_alloc"
    assert rule.action == "alert"
    assert dict(rule.match) == {}


def test_parse_execve_hook() -> None:
    policy = parse_policy(
        {
            "name": "exec-guard",
            "hooks": [{"hook": "execve", "action": "block", "match": {"binary": "/bin/sh"}}],
        }
    )
    rule = policy.hooks[0]
    assert rule.hook == "execve"
    assert dict(rule.match) == {"binary": "/bin/sh"}


def test_parse_openat_hook() -> None:
    policy = parse_policy(
        {
            "name": "file-guard",
            "hooks": [{"hook": "openat", "action": "alert", "match": {"path": "/etc/shadow"}}],
        }
    )
    rule = policy.hooks[0]
    assert rule.hook == "openat"
    assert dict(rule.match) == {"path": "/etc/shadow"}


def test_parse_is_deterministic() -> None:
    data = {
        "name": "egress",
        "hooks": [
            {
                "hook": "tcp_connect",
                "action": "block",
                "match": {"remote_port": 443, "remote_host": "example.internal"},
            },
            {"hook": "sk_alloc", "action": "alert"},
        ],
    }
    first = parse_policy(data)
    second = parse_policy(data)
    assert first == second
    assert first.hooks == second.hooks


def test_rejects_unknown_hook_name() -> None:
    with pytest.raises(ValueError, match="invalid hook 'sendto'"):
        parse_policy({"name": "p", "hooks": [{"hook": "sendto", "action": "block"}]})


def test_rejects_bad_action() -> None:
    with pytest.raises(ValueError, match="invalid action 'drop'"):
        parse_policy({"name": "p", "hooks": [{"hook": "execve", "action": "drop"}]})


def test_rejects_match_key_not_valid_for_hook() -> None:
    with pytest.raises(ValueError, match="'binary' is not valid for hook 'tcp_connect'"):
        parse_policy(
            {
                "name": "p",
                "hooks": [
                    {
                        "hook": "tcp_connect",
                        "action": "block",
                        "match": {"binary": "/bin/sh"},
                    }
                ],
            }
        )


def test_rejects_match_filter_on_sk_alloc() -> None:
    with pytest.raises(ValueError, match="not valid for hook 'sk_alloc'"):
        parse_policy(
            {
                "name": "p",
                "hooks": [
                    {
                        "hook": "sk_alloc",
                        "action": "alert",
                        "match": {"remote_port": 80},
                    }
                ],
            }
        )


@pytest.mark.parametrize("port", [0, 65536, 99999, -1])
def test_rejects_out_of_range_remote_port(port: int) -> None:
    with pytest.raises(ValueError, match="remote_port"):
        parse_policy(
            {
                "name": "p",
                "hooks": [
                    {
                        "hook": "tcp_connect",
                        "action": "block",
                        "match": {"remote_port": port},
                    }
                ],
            }
        )


def test_rejects_boolean_remote_port() -> None:
    with pytest.raises(ValueError, match="remote_port must be an int"):
        parse_policy(
            {
                "name": "p",
                "hooks": [
                    {
                        "hook": "tcp_connect",
                        "action": "block",
                        "match": {"remote_port": True},
                    }
                ],
            }
        )


def test_rejects_missing_name() -> None:
    with pytest.raises(ValueError, match="'name' must be a non-empty string"):
        parse_policy({"hooks": [{"hook": "sk_alloc", "action": "alert"}]})


def test_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="'name' must be a non-empty string"):
        parse_policy({"name": "", "hooks": [{"hook": "sk_alloc", "action": "alert"}]})


def test_rejects_empty_hooks() -> None:
    with pytest.raises(ValueError, match="'hooks' must be a non-empty list"):
        parse_policy({"name": "p", "hooks": []})


def test_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValueError, match="unknown top-level keys"):
        parse_policy(
            {
                "name": "p",
                "hooks": [{"hook": "sk_alloc", "action": "alert"}],
                "extra": 1,
            }
        )


def test_rejects_unknown_hook_key() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        parse_policy(
            {
                "name": "p",
                "hooks": [{"hook": "sk_alloc", "action": "alert", "bogus": 1}],
            }
        )


def test_policy_and_rules_are_hashable() -> None:
    """frozen=True must mean actually hashable (regression: match was an unhashable dict)."""
    policy = parse_policy(
        {
            "name": "p",
            "hooks": [
                {
                    "hook": "tcp_connect",
                    "action": "block",
                    "match": {"remote_port": 443, "remote_host": "10.0.0.0/8"},
                },
                {"hook": "execve", "action": "alert", "match": {"binary": "/bin/sh"}},
            ],
        }
    )

    assert hash(policy) == hash(policy)
    assert len(set(policy.hooks)) == 2
    assert {policy: "ok"}[policy] == "ok"


@pytest.mark.parametrize("bad_match", [[], "x", 5])
def test_rejects_non_mapping_match(bad_match: object) -> None:
    with pytest.raises(ValueError, match="match must be a mapping"):
        parse_policy({"name": "p", "hooks": [{"hook": "tcp_connect", "action": "alert", "match": bad_match}]})


@pytest.mark.parametrize(
    ("hook", "key", "value", "msg"),
    [
        ("execve", "binary", "", "binary must be a non-empty string"),
        ("execve", "binary", 123, "binary must be a non-empty string"),
        ("openat", "path", "", "path must be a non-empty string"),
        ("tcp_connect", "remote_host", 10, "remote_host must be a non-empty string"),
    ],
)
def test_rejects_empty_or_non_string_filter(hook: str, key: str, value: object, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        parse_policy({"name": "p", "hooks": [{"hook": hook, "action": "block", "match": {key: value}}]})
