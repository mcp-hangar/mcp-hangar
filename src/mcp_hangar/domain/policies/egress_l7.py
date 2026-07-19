"""L7 egress policy: deterministic tool-call and argument enforcement.

This is the core-side half of ``MCPEgressPolicy`` (operator epic #53, ADR-013).
The operator enforces L3/L4 (which upstream hosts a server may reach); this
module enforces the L7 semantics *on the connections Hangar already proxies*:

- **Tool-call matching** -- glob on the MCP tool name, resolving to allow / deny
  / require-approval, with a policy-level default action for names no rule
  matches.
- **Argument scanning** -- deterministic secret-pattern detection and a payload
  size limit on tool-call arguments.

It is intentionally **pure and deterministic**: no I/O, no ML, no heuristics
that need tuning. Full DLP and ML-based classification are explicit non-goals
(see ADR-013 and the repo positioning). Secret detection reuses the same
value-regexes as the output redactor, so what the redactor masks on the way out
is what this refuses on the way in.

Wiring this evaluator into the tool-invocation path (fed by the operator's
compiled policy document over the existing config-pull channel) is a follow-up;
this module is the engine and its contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatchcase
import json
import re
from typing import Any

from ..security.redactor import OutputRedactor


class ToolAction(str, Enum):
    """Outcome of evaluating a tool call against a policy."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


# --- Secret-pattern groups -------------------------------------------------
#
# The policy names *groups* (e.g. "aws-keys"); each maps to one or more compiled
# value-regexes. We source the regexes from the output redactor by name so the
# two stay in lockstep, and add PEM private-key blocks, which the redactor does
# not carry.

_BUILTIN: dict[str, re.Pattern] = {p.name: p.pattern for p in OutputRedactor.BUILTIN_PATTERNS}

_PEM_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def _group(*names: str) -> tuple[re.Pattern, ...]:
    return tuple(_BUILTIN[n] for n in names)


SECRET_PATTERN_GROUPS: dict[str, tuple[re.Pattern, ...]] = {
    "aws-keys": _group("aws_access_key"),
    "jwt": _group("jwt_token"),
    "pem-blocks": (_PEM_PRIVATE_KEY,),
    "github-tokens": _group(
        "github_pat",
        "github_oauth",
        "github_user_token",
        "github_server_token",
        "github_refresh_token",
        "github_fine_grained_pat",
    ),
    "stripe-keys": _group(
        "stripe_live_key",
        "stripe_test_key",
        "stripe_restricted_key",
        "stripe_restricted_test_key",
    ),
    "slack-tokens": _group("slack_token"),
    "google-api-keys": _group("google_api_key"),
    "bearer-tokens": _group("bearer_token"),
    "npm-tokens": _group("npm_token"),
    "pypi-tokens": _group("pypi_token"),
}

KNOWN_SECRET_PATTERN_GROUPS: frozenset[str] = frozenset(SECRET_PATTERN_GROUPS)


@dataclass(frozen=True)
class ToolRules:
    """Glob rules over MCP tool names. Precedence: deny > require_approval > allow."""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    require_approval: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArgumentRules:
    """Deterministic constraints on tool-call arguments."""

    secret_patterns: tuple[str, ...] = ()
    max_payload_bytes: int | None = None


@dataclass(frozen=True)
class L7Policy:
    """The L7 slice of an MCPEgressPolicy, resolved for one target.

    default_action is the policy's spec.defaultAction: the outcome for a tool
    name that no rule in ``tools`` matches.
    """

    tools: ToolRules = field(default_factory=ToolRules)
    arguments: ArgumentRules = field(default_factory=ArgumentRules)
    default_action: ToolAction = ToolAction.DENY

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> L7Policy:
        """Parse the wire form the operator compiles from an MCPEgressPolicy.

        Wire shape (camelCase, matching the CRD)::

            {
              "tools": {"allow": [...], "deny": [...], "requireApproval": [...]},
              "arguments": {"secretPatterns": [...], "maxPayloadBytes": 262144},
              "defaultAction": "Deny"
            }

        Missing sections default to empty; ``defaultAction`` defaults to Deny.
        Raises ValueError on a malformed payload.
        """
        if not isinstance(data, dict):
            raise ValueError("L7 policy must be a JSON object")

        default_raw = str(data.get("defaultAction", "Deny")).lower()
        if default_raw not in ("allow", "deny"):
            raise ValueError(f"invalid defaultAction {data.get('defaultAction')!r} (want Allow|Deny)")

        tools_d = data.get("tools") or {}
        args_d = data.get("arguments") or {}
        if not isinstance(tools_d, dict) or not isinstance(args_d, dict):
            raise ValueError("L7 policy 'tools' and 'arguments' must be objects")

        def _globs(key: str) -> tuple[str, ...]:
            raw = tools_d.get(key) or []
            if not isinstance(raw, list) or not all(isinstance(g, str) for g in raw):
                raise ValueError(f"tools.{key} must be a list of strings")
            return tuple(raw)

        secret_patterns = args_d.get("secretPatterns") or []
        if not isinstance(secret_patterns, list) or not all(isinstance(p, str) for p in secret_patterns):
            raise ValueError("arguments.secretPatterns must be a list of strings")

        max_bytes = args_d.get("maxPayloadBytes")
        if max_bytes is not None and (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0):
            raise ValueError("arguments.maxPayloadBytes must be a non-negative integer")

        return cls(
            tools=ToolRules(allow=_globs("allow"), deny=_globs("deny"), require_approval=_globs("requireApproval")),
            arguments=ArgumentRules(secret_patterns=tuple(secret_patterns), max_payload_bytes=max_bytes),
            default_action=ToolAction(default_raw),
        )


@dataclass(frozen=True)
class Decision:
    """The evaluated outcome, with human-readable reasons (audit-friendly)."""

    action: ToolAction
    reasons: tuple[str, ...] = ()


def evaluate_tool(tool_name: str, rules: ToolRules, default_action: ToolAction) -> tuple[ToolAction, str]:
    """Resolve a tool name to an action by glob precedence: deny, then
    require-approval, then allow; if nothing matches, the policy default.
    """
    if any(fnmatchcase(tool_name, g) for g in rules.deny):
        return ToolAction.DENY, f"tool {tool_name!r} matched a deny rule"
    if any(fnmatchcase(tool_name, g) for g in rules.require_approval):
        return ToolAction.REQUIRE_APPROVAL, f"tool {tool_name!r} matched a require-approval rule"
    if any(fnmatchcase(tool_name, g) for g in rules.allow):
        return ToolAction.ALLOW, f"tool {tool_name!r} matched an allow rule"
    return default_action, f"tool {tool_name!r} matched no rule; applying default action"


def _serialize_arguments(arguments: Any) -> str | None:
    """Canonicalize tool-call arguments to a string for size and secret scanning.

    A string is used as-is; anything else is JSON-serialized deterministically
    (sorted keys), with non-JSON values coerced via ``str``. Returns None if the
    payload cannot be serialized at all (e.g. a circular reference) so the caller
    can fail closed rather than crash -- tool arguments arrive as decoded JSON in
    practice, so this only guards against pathological internal callers.
    """
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


def scan_arguments(arguments: Any, rules: ArgumentRules) -> list[str]:
    """Return a list of violation reasons for a tool call's arguments.

    Empty list means the arguments are clean. Unknown secret-pattern group names
    are ignored (they should be caught by CRD validation, not fail closed here).
    Arguments that cannot be serialized for inspection fail closed (a violation).
    """
    # Nothing to check -- avoid serializing (and any cost/crash) when unconstrained.
    if not rules.secret_patterns and rules.max_payload_bytes is None:
        return []

    payload = _serialize_arguments(arguments)
    if payload is None:
        return ["arguments could not be serialized for policy inspection"]

    violations: list[str] = []

    if rules.max_payload_bytes is not None:
        size = len(payload.encode("utf-8"))
        if size > rules.max_payload_bytes:
            violations.append(f"argument payload {size} bytes exceeds limit of {rules.max_payload_bytes}")

    for group in rules.secret_patterns:
        patterns = SECRET_PATTERN_GROUPS.get(group)
        if not patterns:
            continue
        if any(p.search(payload) for p in patterns):
            violations.append(f"arguments contain a secret matching {group!r}")

    return violations


def evaluate(tool_name: str, arguments: Any, policy: L7Policy) -> Decision:
    """Evaluate a full tool call against a policy.

    A secret or oversized payload in the arguments DENIES the call even when the
    tool itself would be allowed or gated for approval -- deny always wins.
    """
    action, reason = evaluate_tool(tool_name, policy.tools, policy.default_action)
    reasons = [reason]

    if action is not ToolAction.DENY:
        violations = scan_arguments(arguments, policy.arguments)
        if violations:
            action = ToolAction.DENY
            reasons.extend(violations)

    return Decision(action=action, reasons=tuple(reasons))
