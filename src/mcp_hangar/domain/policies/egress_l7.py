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

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
import json
import logging
import re
from typing import Any

from ..._sdk_compat import is_modern_protocol_version
from ...redactor import OutputRedactor

logger = logging.getLogger(__name__)


class ToolAction(StrEnum):
    """Outcome of evaluating a tool call against a policy."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyMode(StrEnum):
    """How a policy's verdict is applied (ADR-013).

    - ``ENFORCE`` blocks: a DENY/REQUIRE_APPROVAL verdict stops the call.
    - ``AUDIT`` observes: the same verdict is recorded but the call proceeds.

    ``ENFORCE`` is the safe default. A programmatically-built policy with no
    mode, or a mode-less/unrecognized wire payload from an older operator,
    resolves to ``ENFORCE`` -- it keeps blocking (fail-closed).
    """

    AUDIT = "Audit"
    ENFORCE = "Enforce"


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


#: The SEP-2243 prefix every parameter header carries. Matched
#: case-insensitively: HTTP header names are not case-sensitive, and the
#: operator writes them in the CRD in whatever case reads best.
MCP_PARAM_PREFIX = "mcp-param-"

#: The header a request states its revision in. Read here rather than trusted
#: from negotiation: `_meta` defaults to the supported (modern) version when
#: absent, which would make a handshake-era request look modern to the gate.
PROTOCOL_VERSION_HEADER = "mcp-protocol-version"


@dataclass(frozen=True)
class HeaderMatch:
    """Globs over one ``Mcp-Param-<Token>`` header's value."""

    name: str
    values: tuple[str, ...] = ()

    def matches(self, headers: Mapping[str, str]) -> bool:
        value = headers.get(self.name.lower())
        return value is not None and any(fnmatchcase(value, g) for g in self.values)


@dataclass(frozen=True)
class HeaderRules:
    """``Mcp-Param-*`` selectors, same precedence as the tool-name globs.

    Region, tenant and priority -- the SEP's own examples -- are the dimensions
    L7 egress wants, and they are on the wire without parsing the body. Reading
    them here is what keeps this a header matcher rather than DPI.
    """

    allow: tuple[HeaderMatch, ...] = ()
    deny: tuple[HeaderMatch, ...] = ()
    require_approval: tuple[HeaderMatch, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.allow or self.deny or self.require_approval)


@dataclass(frozen=True)
class ArgumentRules:
    """Deterministic constraints on tool-call arguments."""

    secret_patterns: tuple[str, ...] = ()
    max_payload_bytes: int | None = None


def _header_matches(headers_d: dict[str, Any], key: str) -> tuple[HeaderMatch, ...]:
    """Parse one ``headers.<key>`` list from the wire form. Raises ValueError."""
    raw = headers_d.get(key) or []
    if not isinstance(raw, list):
        raise ValueError(f"headers.{key} must be a list of objects")
    out: list[HeaderMatch] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"headers.{key} entries must be objects")
        name = entry.get("name")
        values = entry.get("values") or []
        if not isinstance(name, str) or not name:
            raise ValueError(f"headers.{key}: 'name' must be a non-empty string")
        # Only Mcp-Param-* is selectable. Any other header is one the policy
        # author does not own -- Authorization above all -- and a selector on it
        # would turn an egress policy into a way to read credentials out of a
        # request by writing globs until one matches.
        if not name.lower().startswith(MCP_PARAM_PREFIX):
            raise ValueError(f"headers.{key}: {name!r} is not an Mcp-Param-* header")
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"headers.{key}: 'values' must be a list of strings")
        if not values:
            raise ValueError(f"headers.{key}: {name!r} has no values to match")
        out.append(HeaderMatch(name=name, values=tuple(values)))
    return tuple(out)


@dataclass(frozen=True)
class L7Policy:
    """The L7 slice of an MCPEgressPolicy, resolved for one target.

    default_action is the policy's spec.defaultAction: the outcome for a tool
    name that no rule in ``tools`` matches.
    """

    tools: ToolRules = field(default_factory=ToolRules)
    headers: HeaderRules = field(default_factory=HeaderRules)
    arguments: ArgumentRules = field(default_factory=ArgumentRules)
    default_action: ToolAction = ToolAction.DENY
    mode: PolicyMode = PolicyMode.ENFORCE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> L7Policy:
        """Parse the wire form the operator compiles from an MCPEgressPolicy.

        Wire shape (camelCase, matching the CRD)::

            {
              "tools": {"allow": [...], "deny": [...], "requireApproval": [...]},
              "arguments": {"secretPatterns": [...], "maxPayloadBytes": 262144},
              "defaultAction": "Deny",
              "mode": "Audit"
            }

        Missing sections default to empty; ``defaultAction`` defaults to Deny.
        ``mode`` accepts ``"Audit"``/``"Enforce"`` (case-sensitive, as sent);
        when absent or unrecognized it defaults to ``Enforce`` -- a mode-less
        payload from an older operator keeps blocking (fail-closed).
        Raises ValueError on a malformed payload.
        """
        if not isinstance(data, dict):
            raise ValueError("L7 policy must be a JSON object")

        default_raw = str(data.get("defaultAction", "Deny")).lower()
        if default_raw not in ("allow", "deny"):
            raise ValueError(f"invalid defaultAction {data.get('defaultAction')!r} (want Allow|Deny)")

        tools_d = data.get("tools") or {}
        headers_d = data.get("headers") or {}
        args_d = data.get("arguments") or {}
        if not isinstance(tools_d, dict) or not isinstance(args_d, dict) or not isinstance(headers_d, dict):
            raise ValueError("L7 policy 'tools', 'headers' and 'arguments' must be objects")

        def _globs(key: str) -> tuple[str, ...]:
            raw = tools_d.get(key) or []
            if not isinstance(raw, list) or not all(isinstance(g, str) for g in raw):
                raise ValueError(f"tools.{key} must be a list of strings")
            return tuple(raw)

        secret_patterns = args_d.get("secretPatterns") or []
        if not isinstance(secret_patterns, list) or not all(isinstance(p, str) for p in secret_patterns):
            raise ValueError("arguments.secretPatterns must be a list of strings")

        # A group name this build does not know detects nothing. Rejecting the
        # policy at parse time is the only point where that is visible: at scan
        # time the unknown group is just absent, so the policy reports enforcing
        # while a detector the author asked for is silently off.
        #
        # The CRD does not catch this. spec...secretPatterns is declared as
        # `items: {type: string}` with maxItems, and carries no enum, so
        # `github-token` (singular) is accepted by the API server and lands here
        # intact. Validating here also covers the REST channel, which the CRD
        # never sees at all.
        unknown = sorted(set(secret_patterns) - KNOWN_SECRET_PATTERN_GROUPS)
        if unknown:
            known = ", ".join(sorted(KNOWN_SECRET_PATTERN_GROUPS))
            raise ValueError(f"unknown arguments.secretPatterns group(s): {', '.join(unknown)} (known groups: {known})")

        max_bytes = args_d.get("maxPayloadBytes")
        if max_bytes is not None and (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0):
            raise ValueError("arguments.maxPayloadBytes must be a non-negative integer")

        # Mode is parsed exactly as sent ("Audit"/"Enforce"); anything absent or
        # unrecognized fails closed to Enforce (keeps blocking).
        mode = PolicyMode.AUDIT if data.get("mode") == "Audit" else PolicyMode.ENFORCE

        return cls(
            tools=ToolRules(allow=_globs("allow"), deny=_globs("deny"), require_approval=_globs("requireApproval")),
            headers=HeaderRules(
                allow=_header_matches(headers_d, "allow"),
                deny=_header_matches(headers_d, "deny"),
                require_approval=_header_matches(headers_d, "requireApproval"),
            ),
            arguments=ArgumentRules(secret_patterns=tuple(secret_patterns), max_payload_bytes=max_bytes),
            default_action=ToolAction(default_raw),
            mode=mode,
        )

    def to_wire(self) -> dict[str, Any]:
        """Serialize back to the wire form ``from_dict`` parses.

        This is what the fleet snapshot persists (#991): the policy has to
        survive a restart and reach peer replicas, and the wire shape is the
        one representation every producer and consumer already agrees on.
        Round-trip invariant: ``L7Policy.from_dict(p.to_wire()) == p``.
        """
        return {
            "tools": {
                "allow": list(self.tools.allow),
                "deny": list(self.tools.deny),
                "requireApproval": list(self.tools.require_approval),
            },
            "headers": {
                key: [{"name": m.name, "values": list(m.values)} for m in matches]
                for key, matches in (
                    ("allow", self.headers.allow),
                    ("deny", self.headers.deny),
                    ("requireApproval", self.headers.require_approval),
                )
            },
            "arguments": {
                "secretPatterns": list(self.arguments.secret_patterns),
                "maxPayloadBytes": self.arguments.max_payload_bytes,
            },
            "defaultAction": self.default_action.value.capitalize(),
            "mode": "Audit" if self.mode is PolicyMode.AUDIT else "Enforce",
        }


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


def evaluate_headers(
    headers: Mapping[str, str] | None,
    rules: HeaderRules,
) -> tuple[ToolAction, str] | None:
    """Resolve ``Mcp-Param-*`` selectors, or ``None`` when none applies.

    Precedence is the tool-name ladder: deny, then require-approval, then
    allow. ``None`` means no rule matched -- it is not an allow, so the caller
    falls through to the tool verdict and, failing that, the policy default.

    A request whose ``MCP-Protocol-Version`` predates mandatory header-body
    validation never satisfies a selector. On such a revision nothing has
    checked that the header agrees with the body, so a caller can route on one
    value and execute another; SEP-2243 says an intermediary enforcing policy
    on mirrored headers should verify the revision and refuse to trust it
    otherwise. Refusing to *match* is the honest shape of that here: the
    request is left to the tool rules and the default action, rather than being
    handed an allow it did not earn or a deny some other caller's header wrote.
    """
    if not rules:
        return None
    if headers is None or not is_modern_protocol_version(headers.get(PROTOCOL_VERSION_HEADER)):
        return None

    for matches, action, label in (
        (rules.deny, ToolAction.DENY, "deny"),
        (rules.require_approval, ToolAction.REQUIRE_APPROVAL, "require-approval"),
        (rules.allow, ToolAction.ALLOW, "allow"),
    ):
        hit = next((m for m in matches if m.matches(headers)), None)
        if hit is not None:
            return action, f"header {hit.name!r} matched an {label} rule"
    return None


def _serialize_arguments(arguments: Any) -> str | None:
    """Canonicalize tool-call arguments to a string for size and secret scanning.

    A string is used as-is; anything else is JSON-serialized deterministically
    (sorted keys), with non-JSON values coerced via ``str``. Returns None if the
    payload cannot be serialized at all, so the caller can fail closed rather
    than crash.

    Caught by class rather than by name, which the earlier
    ``except (TypeError, ValueError)`` did not do. Two things it missed, both
    reachable from a tool call:

    * ``RecursionError`` from nesting the encoder cannot walk. About 992 levels
      does it -- roughly 7 KB of JSON, so ``maxPayloadBytes`` is no defence:
      the size check runs on the string this function returns, i.e. behind the
      thing that breaks.
    * anything ``default=str`` raises. That calls an arbitrary ``__str__``,
      which is somebody else's code.

    A serialization failure is not silent: the reason goes to the log, while the
    caller's verdict stays the deliberately unspecific "could not be serialized"
    -- what an operator needs in an audit record is that the call was refused
    uninspected, and the encoder's own message belongs in the log beside it.
    """
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception as exc:  # noqa: BLE001 -- a policy decision must not depend on the encoder
        logger.warning(
            "argument_serialization_failed error=%s: %s -- the call is refused uninspected",
            type(exc).__name__,
            exc,
        )
        return None


def scan_arguments(arguments: Any, rules: ArgumentRules) -> list[str]:
    """Return a list of violation reasons for a tool call's arguments.

    Empty list means the arguments are clean. Arguments that cannot be
    serialized for inspection fail closed (a violation).

    Unknown secret-pattern group names are skipped here, but that is a residual
    safety net rather than the contract: ``L7Policy.from_dict`` rejects a policy
    naming a group this build does not know, so an unknown group should be
    unreachable at scan time. It used to be reachable, and the old docstring
    deferred to "CRD validation" that does not exist -- the CRD declares
    secretPatterns as a plain string array with no enum.
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


def evaluate(
    tool_name: str,
    arguments: Any,
    policy: L7Policy,
    headers: Mapping[str, str] | None = None,
) -> Decision:
    """Evaluate a full tool call against a policy.

    A secret or oversized payload in the arguments DENIES the call even when the
    tool itself would be allowed or gated for approval -- deny always wins.

    ``headers`` carries this request's ``Mcp-Param-*`` values plus its
    ``MCP-Protocol-Version``, lower-cased. An ``Mcp-Param-*`` selector that
    matches decides the call the way a tool-name rule does, and takes
    precedence over the tool ladder: it is the more specific statement, and it
    is the one an operator writes to say "this region, never". A request that
    matches no selector -- including every handshake-era request, which cannot
    be trusted to have had its headers checked against its body -- is resolved
    by the tool rules alone.
    """
    header_verdict = evaluate_headers(headers, policy.headers)
    if header_verdict is not None:
        action, reason = header_verdict
        reasons = [reason]
    else:
        action, reason = evaluate_tool(tool_name, policy.tools, policy.default_action)
        reasons = [reason]

    if action is not ToolAction.DENY:
        try:
            violations = scan_arguments(arguments, policy.arguments)
        except Exception as exc:  # noqa: BLE001 -- see below
            # This function is total by contract. A tool call that cannot be
            # inspected must end in a verdict the caller can act on, because
            # an exception here is not fail-closed: it aborts the call before
            # dispatch in EVERY mode, which silently turns Audit into a hard
            # block (ADR-013 promises Audit observes and lets the call
            # through), and in Enforce produces no `EgressPolicyDeniedError`,
            # no `EgressPolicyViolationObserved` and no audit entry -- the call
            # dies unattributed. Denying here keeps Enforce blocking with a
            # reason, and lets Audit record and proceed.
            logger.exception("argument_inspection_failed error=%s", type(exc).__name__)
            violations = ["arguments could not be inspected for policy violations"]
        if violations:
            action = ToolAction.DENY
            reasons.extend(violations)

    return Decision(action=action, reasons=tuple(reasons))
