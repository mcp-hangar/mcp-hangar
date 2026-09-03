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
anything deeper.

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
        "members",
        "min_healthy",
        "mode",
        "network",
        "read_only",
        "resources",
        "strategy",
        "tls",
        "tool_access",
        "tool_projection",
        "tools",
        "transport",
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
    "discovery": frozenset({"auto_register", "enabled", "refresh_interval_s", "security", "sources"}),
    "event_store": frozenset({"allow_memory_fallback", "driver", "enabled", "path"}),
    "execution": frozenset({"default_mcp_server_concurrency", "max_concurrency"}),
    # `param_validation.required` (ADR-025). Global to the front door: the
    # condition is a property of the request, not of one upstream.
    "headers": frozenset({"param_validation"}),
    "hot_loading": frozenset({"cache", "enabled", "registry"}),
    "interceptors": frozenset({"validators"}),
    "logging": frozenset({"file", "json_format", "level"}),
    "observability": frozenset({"langfuse", "tracing"}),
    "persistence": frozenset({"backend", "postgresql", "sqlite"}),
    # The command-bus limit, read by `bootstrap/runtime.resolve_rate_limit_config`.
    # There is a second `rate_limit` nested under `auth`; both spellings are live
    # and the only thing that tells them apart is which one you nested it in.
    "rate_limit": frozenset({"burst", "rps"}),
    "relay_tasks_enabled": None,  # a bool, not a section
    # `max_per_tenant` (#1146), read by `config._init_resource_links_from_config`.
    "resource_links": frozenset({"max_per_tenant"}),
    "retry": frozenset({"default_policy", "per_mcp_server"}),
    "startup_checks": frozenset({"enforce"}),
    "tool_access": frozenset({"mode", "rules"}),
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


def validate_config(config: dict[str, Any]) -> list[str]:
    """Every key in *config* that no reader looks for, as one message each."""
    if not isinstance(config, dict):
        return []

    problems = _unknown("config", config, frozenset(SECTIONS))

    for name, allowed in SECTIONS.items():
        if allowed is None or name not in config:
            continue
        problems += _unknown(f"{name}", config[name], allowed)

    servers = config.get("mcp_servers")
    if isinstance(servers, dict):
        for server_id, spec in servers.items():
            problems += _unknown(f"mcp_servers.{server_id}", spec, SERVER_SPEC_KEYS)

    return problems
