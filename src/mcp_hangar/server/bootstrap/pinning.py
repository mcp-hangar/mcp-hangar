"""Refuse a digest-pinning configuration that cannot enforce anything (#902).

Digest pins were addressable only per tenant, and a tenant id reaches the
enforcement path from exactly one place: `Principal.tenant_id`. With auth
disabled every caller is anonymous, `tenant_id` is `None` on every request, and
`ToolProjectionRegistry.resolve_pin` matched no per-tenant pin -- so the gate
took its "no pin" branch and every call went through unverified, while
`initialize` kept advertising `io.mcp-hangar.digest-pinning` with its three
enforcement modes.

Nothing was wrong with the pins in the file. They were simply addressed to a
tenant that could never turn up, and the only signal was silence.

Asked on the axis the operator controls, and asked at boot: per-tenant pins plus
`auth.enabled: false` is a statement that contradicts itself, and the operator
who wrote both halves is the one who can resolve it. The all-tenants `pins:`
block added alongside this refusal is what makes the refusal answerable -- it
holds a caller with no tenant identity, so a deployment that runs without auth
has somewhere to go other than turning the feature off.
"""

from __future__ import annotations

from typing import Any


class PinnedToolsNeedAnIdentityError(RuntimeError):
    """Per-tenant digest pins were declared where no caller can carry a tenant.

    `auth.enabled` is false, so the identity middleware binds the anonymous
    principal on every request and `tenant_id` is `None` throughout. Every pin
    under `tenant_overrides.<tenant>.pins` is keyed by a tenant id, so none of
    them can ever be looked up: drift stays computable and nothing stops it.

    Not a warning. A gateway that boots here reports a healthy start, answers
    every call, and enforces an integrity control it also advertises -- which is
    the shape of defect that costs an audit rather than a restart.
    """

    def __init__(self, offenders: list[tuple[str, str, str]]) -> None:
        self.offenders = offenders
        named = ", ".join(f"{server}.{tenant}.{tool}" for server, tenant, tool in sorted(offenders)[:5])
        more = "" if len(offenders) <= 5 else f" (and {len(offenders) - 5} more)"
        super().__init__(
            f"digest pins are declared per tenant ({named}{more}) and authentication is disabled "
            "(`auth.enabled: false`), so no caller carries a tenant id and not one of those pins can "
            "ever be matched -- schema drift would be counted and nothing would be stopped. Either "
            "enable authentication so callers arrive with the tenant these pins name, or move them to "
            "the all-tenants `tool_projection.pins:` block, which holds every caller including an "
            "anonymous one."
        )


def _auth_is_enabled(config: dict[str, Any]) -> bool:
    """Whether the configuration turns authentication on.

    Mirrors `parse_auth_config`, which defaults `enabled` to False: auth is
    opt-in, so an absent block means anonymous callers and a `None` tenant.
    """
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return False
    return bool(auth.get("enabled", False))


def _per_tenant_pins(config: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every `(server, tenant, tool)` pinned under a tenant override.

    Reads the raw document rather than the registry so this can run before the
    servers are built -- the same reason `refuse_local_modes_in_a_declared_cluster`
    reads configuration directly.
    """
    found: list[tuple[str, str, str]] = []
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return found
    for server_id, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        projection = spec.get("tool_projection")
        if not isinstance(projection, dict):
            continue
        overrides = projection.get("tenant_overrides")
        if not isinstance(overrides, dict):
            continue
        for tenant_id, tenant_spec in overrides.items():
            if not isinstance(tenant_spec, dict):
                continue
            pins = tenant_spec.get("pins")
            if not isinstance(pins, dict):
                continue
            found.extend((str(server_id), str(tenant_id), str(tool)) for tool in pins)
    return found


def refuse_pins_that_no_caller_can_match(config: dict[str, Any] | None = None) -> None:
    """Refuse per-tenant digest pins on a gateway with authentication off.

    All-tenants pins (`tool_projection.pins:`) are deliberately not refused:
    those are exactly the ones an anonymous caller can be held to, so they are
    the way out of this refusal rather than another instance of it.

    Reads configuration only, so it runs before anything is built.

    Args:
        config: Full configuration document.

    Raises:
        PinnedToolsNeedAnIdentityError: When per-tenant pins are declared and
            authentication is disabled.
    """
    config = config or {}
    if _auth_is_enabled(config):
        return
    offenders = _per_tenant_pins(config)
    if offenders:
        raise PinnedToolsNeedAnIdentityError(offenders)
