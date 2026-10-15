"""Reject a `config.yaml` key that nothing reads.

`config.yaml` had no schema: unknown keys were kept and ignored at every level,
so `commandd: [python]` built a subprocess server with no command, `idle_tt1_s`
applied nothing, and `auth: {enabledd: true}` was a deployment that believed it
had enabled authentication. The failure arrived later and somewhere else --
`ensure_ready` reporting a subprocess that will not start reads like a broken
server, not a misspelled key (#982).

The policy DSL in this codebase already does the right thing (`domain/policies/
dsl.py` raises on unknown hook keys, naming the allowed set). This applies that
shape to the surface every user actually touches.

## How deep this goes, and why it stops there

Validated: **top-level section names**, the **direct child keys of each
section**, and the keys of an **`mcp_servers.<id>` spec**. Not validated:
anything deeper, with one exception: a group's `circuit_breaker.reset_timeout_s`,
which was removed (#1398). It is one named key with a known history, not a guess
at the readers below the spec.

That line is not a guess about where typos happen -- it is where a single
reader exists to enumerate from. Sections are dispatched in `server/bootstrap`,
a section's own keys are read explicitly by that section's bootstrap module
(`coordination.py` reads exactly `lease_ttl_s`, `renew_interval_s`,
`renew_deadline_s`), and a server spec is read in `_load_mcp_server_config`.
Below that the keys live in ~20 modules with no single registry, and a schema
hand-copied from twenty readers is a second source of truth that drifts. A
drifted schema **rejects a valid config**, which is strictly worse than
accepting a typo: one is a gateway that will not start, the other is a setting
that did not apply.

So a section whose children are an open-ended map -- `mcp_servers`,
`auth.role_assignments`, `persistence.postgresql` -- is listed as opaque
(`None`) rather than guessed at.

## Strictness

`warn` today, `reject` from 3.0.0. Rejecting is correct and is also a breaking
change for anyone carrying a stale key, so this release names the keys in a log
line and starts anyway; `HANGAR_CONFIG_STRICT=1` opts in to the end state now.
`mcp-hangar config check` is always strict -- it is asked the question directly.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["ConfigSchemaError", "strict_mode", "validate_config"]


class ConfigSchemaError(ValueError):
    """A config carries keys nothing reads."""


# Keys of an `mcp_servers.<id>` spec, from `_load_mcp_server_config` and
# `_load_group_config` in `server/config.py`.
SERVER_SPEC_KEYS = frozenset(
    {
        # The per-kind prompt/resource policy block (#1028). Tools keep `tools`,
        # and `resources` below is the container limit block -- not a policy.
        "access",
        "args",
        "auth",
        "auto_start",
        "build",
        "canary",
        "capabilities",
        "circuit_breaker",
        "command",
        "description",
        "endpoint",
        "env",
        "header_exposure",
        "health",
        "health_check_interval_s",
        "http",
        "idle_ttl_s",
        "image",
        "max_concurrency",
        "max_consecutive_failures",
        # The server's own read limit on one upstream response (#1613).
        "max_response_bytes",
        "members",
        "min_healthy",
        "mode",
        "network",
        # The older spelling of `network`, read as its fallback (#1664).
        "network_mode",
        "read_only",
        "resources",
        "strategy",
        "tls",
        "tool_access",
        "tool_projection",
        "tools",
        "transport",
        # `current` runs the container as the gateway's own uid:gid (#1664).
        "user",
        "volumes",
    }
)

# Top-level section -> the keys that section's reader looks for, or None where
# the children are an open-ended map rather than a fixed set. Adding a key here
# without a reader is the failure mode this module exists to prevent, so the
# comment on each opaque entry says who consumes it.
SECTIONS: dict[str, frozenset[str] | None] = {
    # An id -> spec map; specs are checked against SERVER_SPEC_KEYS instead.
    "mcp_servers": None,
    "approvals": frozenset({"channel", "delivery", "enabled", "slack", "webhook"}),
    "auth": frozenset(
        {
            "allow_anonymous",
            "api_key",
            "enabled",
            "oidc",
            "opa",
            "rate_limit",
            "role_assignments",
            # The declared principal for a stdio session (ADR-026), read by
            # `auth/config.parse_auth_config`. Ignored over HTTP.
            "stdio",
            "storage",
        }
    ),
    "config_reload": frozenset({"enabled", "interval_s", "use_watchdog"}),
    "coordination": frozenset({"lease_ttl_s", "renew_deadline_s", "renew_interval_s"}),
    # `lifecycle` holds default_ttl_s, check_interval_s and drain_timeout_s, read by
    # `DiscoveryConfig.from_dict` (#1664).
    "discovery": frozenset({"auto_register", "enabled", "lifecycle", "refresh_interval_s", "security", "sources"}),
    "event_store": frozenset({"allow_memory_fallback", "driver", "enabled", "path"}),
    # `tenant_limits` (#1445), read by `config._init_tenant_limits_from_config`,
    # which checks the keys of each entry itself. `max_response_bytes` (#1613),
    # read by `response_limit.resolve_max_response_bytes`.
    "execution": frozenset(
        {"default_mcp_server_concurrency", "max_concurrency", "max_response_bytes", "tenant_limits"}
    ),
    # `param_validation.required` (ADR-025). Global to the front door: the
    # condition is a property of the request, not of one upstream.
    "headers": frozenset({"param_validation"}),
    "hot_loading": frozenset({"cache", "enabled", "registry"}),
    # `graceful_shutdown_timeout_s` (#1447), read by `config.http_graceful_shutdown_timeout`
    # for `serve --http`, which hands it to uvicorn.
    "http": frozenset({"graceful_shutdown_timeout_s"}),
    "interceptors": frozenset({"validators"}),
    "logging": frozenset({"file", "json_format", "level"}),
    # `audit.enabled` (#1327), read by `bootstrap/observability._parse_observability_config`.
    "observability": frozenset({"audit", "tracing"}),
    "persistence": frozenset({"backend", "postgresql", "sqlite"}),
    # The command-bus limit, read by `bootstrap/runtime.resolve_rate_limit_config`,
    # and `per_caller` (#1471), read by `infrastructure/caller_rate_limit.parse_per_caller`,
    # which checks its own keys. There is a second `rate_limit` nested under `auth`;
    # both spellings are live and the only thing that tells them apart is which
    # one you nested it in.
    "rate_limit": frozenset({"burst", "per_caller", "rps"}),
    "relay_tasks_enabled": None,  # a bool, not a section
    # `max_per_tenant` (#1146), read by `config._init_resource_links_from_config`.
    "resource_links": frozenset({"max_per_tenant"}),
    "retry": frozenset({"default_policy", "per_mcp_server"}),
    # `trusted_networks` (#1752), read by `config._init_ssrf_from_config`.
    "ssrf": frozenset({"trusted_networks"}),
    "startup_checks": frozenset({"enforce"}),
    # `mode`, read by `config._init_topology_mode_from_config`, and
    # `required_catalogue` (#1446), read by `catalogue_readiness.required_catalogue`,
    # which checks its own keys. `rules` was listed here too and never read
    # (#1422): see `_REMOVED_SECTION_KEYS`.
    "tool_access": frozenset({"mode", "required_catalogue"}),
    # `pin_recheck_interval_s` (#1693), read by `pin_recheck.pin_recheck_interval_s`
    # when the workers are built. Not the per-server `tool_projection` block.
    "tool_projection": frozenset({"pin_recheck_interval_s"}),
    "truncation": None,  # TruncationConfig.from_dict owns these
    # `tenants` (ADR-024, #1048), read by `config._init_ui_resources_from_config`.
    # Shipped in 2.13.1 without an entry here, so `HANGAR_CONFIG_STRICT=1` --
    # the posture the docs recommend for CI and staging -- refused to start a
    # gateway whose config declared the block the docs told it to write (#1167).
    "ui_resources": frozenset({"tenants"}),
}


def strict_mode() -> bool:
    """Whether an unknown key refuses the config instead of warning about it."""
    return os.getenv("HANGAR_CONFIG_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}


def _unknown(where: str, present: Any, allowed: frozenset[str]) -> list[str]:
    if not isinstance(present, dict):
        return []
    unknown = sorted(set(present) - allowed)
    if not unknown:
        return []
    return [f"{where} has unknown key(s) {unknown}; allowed keys: {sorted(allowed)}"]


# A group's circuit reset timeout, in both spellings (#1398). The nested one was
# read into the group's breaker and never consulted; the flat one is the old
# `McpServerGroup` keyword and never had a reader in the config. Named rather
# than reported as a typo: whoever wrote it meant it, so the message says why it
# is gone instead of listing the allowed set. It is still a key nothing reads,
# so strict mode and `config check` refuse it like any other.
_REMOVED_GROUP_RESET_TIMEOUT = (
    "has no effect on group {group!r} and was removed: a group's circuit closes once "
    "`min_healthy` members are back in rotation, never on a timer (#1398). Delete the key."
)


def _group_reset_timeouts(spec: dict[str, Any]) -> list[str]:
    """The removed reset timeout keys a group spec sets, dotted under the spec."""
    found = ["circuit_reset_timeout_s"] if "circuit_reset_timeout_s" in spec else []
    breaker = spec.get("circuit_breaker")
    if isinstance(breaker, dict) and "reset_timeout_s" in breaker:
        found.append("circuit_breaker.reset_timeout_s")
    return found


# Section key -> why it was removed, for keys a section once accepted. Named for
# the same reason as the group reset timeout above: whoever wrote the key meant
# it, so the message says why it is gone. Strict mode and `config check` still
# refuse it, as they refuse any key nothing reads.
_REMOVED_SECTION_KEYS: dict[str, dict[str, str]] = {
    "observability": {
        # The adapter was built and never called after 2.22.0 (#1683). Its scrub
        # keys also refuse the boot, in `bootstrap/observability`.
        "langfuse": (
            "was removed with the Langfuse adapter, which nothing called (#1683). Langfuse takes Hangar's "
            "spans over OTLP: set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT to its OTLP traces endpoint, "
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf and the Basic auth header in "
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS (examples/langfuse/README.md). Delete the key."
        ),
    },
    "tool_access": {
        # In the schema since it was written (#984), with no reader behind it:
        # a `rules:` block validated, even under strict mode, and did nothing.
        "rules": (
            "was never read and was removed: the schema accepted it, but it never restricted a tool "
            "(#1422). Tool access is set by the `tools:` allow and deny lists of a server, a group or "
            "a group member, and `tool_access.mode` still selects the topology. Delete the key."
        ),
    },
}


def _section_extras(name: str, section: Any) -> frozenset[str]:
    """Keys a section accepts because of what else it says.

    ``approvals.channel`` names the delivery channel and the channel reads its
    settings from ``approvals.<channel>``. Core knows two channels; the rest are
    installed entry points, so their names cannot be listed here, but the one
    the config selects is right there in the config (#1664).
    """
    if name == "approvals" and isinstance(section, dict) and isinstance(section.get("channel"), str):
        return frozenset({section["channel"]})
    return frozenset()


def _section_problems(name: str, section: Any, allowed: frozenset[str]) -> list[str]:
    removed = _REMOVED_SECTION_KEYS.get(name, {})
    if not isinstance(section, dict):
        return _unknown(name, section, allowed)

    named = [f"{name}.{key} {removed[key]}" for key in sorted(removed) if key in section]
    rest = {key: value for key, value in section.items() if key not in removed}
    return named + _unknown(name, rest, allowed)


#: The keys an access policy block reads, and the kinds of an ``access`` block.
#: Copies of ``domain.model.mcp_server_config._POLICY_KEYS`` and
#: ``server.config._ACCESS_KINDS``, because this module is loaded by path from a
#: source checkout and imports nothing (the docs gate does that); a test holds
#: the copies to the originals.
POLICY_KEYS = frozenset({"allow_list", "deny_list", "approval_list", "approval_timeout_seconds", "approval_channel"})
ACCESS_KINDS = ("prompt", "resource")

_POLICY_HINTS = {
    "allow": "allow_list",
    "deny": "deny_list",
    "approve": "approval_list",
    "approval": "approval_list",
    "approvals": "approval_list",
}


def _policy_block_problems(where: str, block: Any, extra: frozenset[str] = frozenset()) -> list[str]:
    """The keys of one access policy block that no policy reads (#1773).

    ``tools: {allow: [read], deny: [add]}`` booted with no policy at all: the
    parser read the lists it knows and found none. The loader refuses it; this
    is the same refusal for `config check`, which does not run the loader.
    """
    if not isinstance(block, dict):
        return []
    problems = []
    for key in sorted((k for k in block if k not in POLICY_KEYS and k not in extra), key=str):
        hint = _POLICY_HINTS.get(str(key).lower())
        problems.append(
            f"{where}.{key} is not a key an access policy reads"
            + (f" (did you mean {hint}?)" if hint else "")
            + f"; it reads {', '.join(sorted(POLICY_KEYS))}, and an unread key leaves the policy unenforced"
        )
    return problems


def _access_problems(where: str, scope: Any) -> list[str]:
    """The policy blocks under a scope's ``access:`` mapping."""
    access = scope.get("access") if isinstance(scope, dict) else None
    if not isinstance(access, dict):
        return []
    problems: list[str] = []
    for kind in ACCESS_KINDS:
        problems += _policy_block_problems(f"{where}.access.{kind}", access.get(kind))
    return problems


def _policy_problems(where: str, spec: Any) -> list[str]:
    """Every access policy block of one server or group spec: its own, its tenants', its members'."""
    if not isinstance(spec, dict):
        return []
    problems = _policy_block_problems(f"{where}.tools", spec.get("tools"))
    problems += _access_problems(where, spec)

    tool_access = spec.get("tool_access")
    members_by_tenant = tool_access.get("member") if isinstance(tool_access, dict) else None
    if isinstance(members_by_tenant, dict):
        for tenant, member_spec in members_by_tenant.items():
            tenant_where = f"{where}.tool_access.member.{tenant}"
            problems += _policy_block_problems(tenant_where, member_spec, frozenset({"access"}))
            problems += _access_problems(tenant_where, member_spec)

    members = spec.get("members")
    if isinstance(members, list):
        for member in members:
            if isinstance(member, dict):
                problems += _policy_block_problems(f"{where}.members.{member.get('id')}.tools", member.get("tools"))
    return problems


def _server_spec_problems(server_id: str, spec: Any) -> list[str]:
    where = f"mcp_servers.{server_id}"
    if not isinstance(spec, dict) or spec.get("mode") != "group":
        return _unknown(where, spec, SERVER_SPEC_KEYS) + _policy_problems(where, spec)

    message = _REMOVED_GROUP_RESET_TIMEOUT.format(group=server_id)
    problems = [f"{where}.{key} {message}" for key in _group_reset_timeouts(spec)]
    rest = {key: value for key, value in spec.items() if key != "circuit_reset_timeout_s"}
    return problems + _unknown(where, rest, SERVER_SPEC_KEYS) + _policy_problems(where, spec)


def validate_config(config: dict[str, Any]) -> list[str]:
    """Every key in *config* that no reader looks for, as one message each."""
    if not isinstance(config, dict):
        return []

    problems = _unknown("config", config, frozenset(SECTIONS))

    for name, allowed in SECTIONS.items():
        if allowed is None or name not in config:
            continue
        problems += _section_problems(name, config[name], allowed | _section_extras(name, config[name]))

    servers = config.get("mcp_servers")
    if isinstance(servers, dict):
        for server_id, spec in servers.items():
            problems += _server_spec_problems(str(server_id), spec)

    return problems
