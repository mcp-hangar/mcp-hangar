"""Observe, merge and compare tool digests for `mcp-hangar pin` (#1191).

`digest_enforcement` has defaulted to `block` for releases, and a mismatch
already refuses the call. What was missing is the other half: nothing exposed a
computed digest, so writing a pin meant deriving a SHA-256 by hand from a
canonicalization the operator could not see. The feature shipped enforceable and
unreachable.

Digests here come from `compute_tool_digest` -- the same function the projection
registry uses to build what the gate compares against -- so a pin written from
this output matches by construction rather than by two implementations agreeing.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ....domain.model.mcp_server import McpServer
from ....domain.services.digest_computation import compute_tool_digest
from ....domain.value_objects import McpServerState
from ....logging_config import get_logger
from ....server.config import load_configuration
from .smoke_test import DEFAULT_TIMEOUT_SECONDS, MIN_PER_SERVER_TIMEOUT_SECONDS, build_mcp_server

logger = get_logger(__name__)


@dataclass
class Observation:
    """What one server was serving when we asked."""

    mcp_server_id: str
    digests: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    #: The upstream these were read from (`McpServer.upstream_identity`), for the pins to bind to (#1802).
    upstream: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Drift:
    """A tool whose configured pin disagrees with what the server serves."""

    mcp_server_id: str
    tool: str
    expected: str | None  # pin in the config; None when the tool has no pin
    observed: str | None  # digest now; None when the tool is no longer served
    #: For a pin declared on a group: the member that serves *observed*; None when no member serves it.
    member: str | None = None


def is_group(spec: Any) -> bool:
    """Whether an `mcp_servers` entry is a group, tested exactly as the loader tests it."""
    return isinstance(spec, dict) and spec.get("mode", "subprocess") == "group"


def group_members(config: dict[str, Any]) -> dict[str, list[str]]:
    """Each group's member ids, in declaration order, as the loader reads them."""
    members: dict[str, list[str]] = {}
    for group_id, spec in (config.get("mcp_servers") or {}).items():
        if not is_group(spec):
            continue
        ids = [str(m["id"]) for m in spec.get("members") or [] if isinstance(m, dict) and m.get("id")]
        members[str(group_id)] = list(dict.fromkeys(ids))
    return members


def digest_tools(mcp_server: McpServer) -> dict[str, str]:
    """Digest every tool a started server is serving, as `{tool: sha256}`.

    One line, shared with the `init` smoke test, so a pin written during setup
    and a pin written by `mcp-hangar pin` cannot come from two canonicalizations.
    """
    return {schema.name: compute_tool_digest(schema.to_dict()).sha256 for schema in mcp_server.get_tool_schemas()}


def observe_digests(
    config_path: Path,
    mcp_server_ids: list[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Observation]:
    """Start each configured server, read its tools, and digest them.

    Starting is the only way to ask: a tool list is what a server answers, not
    something the configuration knows. Each server is stopped again afterwards --
    this command is not a gateway.

    A group is not a server and is never started: it serves its members' tools.
    Selecting a group (by default every group is selected) observes its members
    instead -- a member declared under `mcp_servers` once, a member defined inline
    from its own entry, as the loader builds it -- so `find_drift` can hold the
    group's own pins against each of them.
    """
    config = load_configuration(str(config_path))
    every_entry: dict[str, Any] = config.get("mcp_servers", {}) or {}
    _stop_what_loading_started(every_entry)

    entries = every_entry
    if mcp_server_ids:
        unknown = [name for name in mcp_server_ids if name not in entries]
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        entries = {name: entries[name] for name in mcp_server_ids}

    servers: dict[str, Any] = {}
    for name, spec in entries.items():
        if not is_group(spec):
            servers.setdefault(name, spec)
            continue
        for member in spec.get("members") or []:
            member_id = member.get("id") if isinstance(member, dict) else None
            if not member_id:
                continue
            declared = every_entry.get(member_id)
            # A member naming a declared server is that server; otherwise it is built from its own entry.
            servers.setdefault(str(member_id), declared if declared is not None and not is_group(declared) else member)

    per_server_timeout = max(timeout_s / len(servers), MIN_PER_SERVER_TIMEOUT_SECONDS) if servers else timeout_s
    return [_observe_one(name, spec, per_server_timeout) for name, spec in servers.items()]


def _stop_what_loading_started(entries: dict[str, Any]) -> None:
    """Stop the members a group's `auto_start` started while the file was loaded.

    The loader is the gateway's, and a gateway starts a group's members as it
    builds the group. This command starts each server it asks itself and stops
    it again; the loader's copies would otherwise keep running until it exits.
    """
    from ....server.state import GROUPS

    for group_id, spec in entries.items():
        group = GROUPS.get(group_id) if is_group(spec) else None
        if group is None:
            continue
        try:
            group.stop_all()
        except Exception:  # noqa: BLE001 -- best-effort cleanup; observing does not depend on it
            logger.warning("pin_group_members_not_stopped", group_id=group_id)


def _observe_one(mcp_server_id: str, spec: dict[str, Any], timeout_s: float) -> Observation:
    mcp_server = None
    try:
        mcp_server = build_mcp_server(mcp_server_id, spec)
        mcp_server.ensure_ready()

        deadline = time.time() + timeout_s
        while mcp_server.state != McpServerState.READY and time.time() < deadline:
            time.sleep(0.1)

        if mcp_server.state != McpServerState.READY:
            return Observation(
                mcp_server_id,
                error=f"did not reach READY within {timeout_s:.0f}s (state: {mcp_server.state.value})",
            )

        return Observation(mcp_server_id, digests=digest_tools(mcp_server), upstream=mcp_server.upstream_identity())
    except Exception as exc:  # noqa: BLE001 -- one unreachable server must not hide the others
        return Observation(mcp_server_id, error=str(exc))
    finally:
        if mcp_server is not None:
            try:
                mcp_server.stop()
            except Exception:  # noqa: BLE001 -- best-effort cleanup; the digests are already read
                pass


def configured_pins(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The all-tenants pins already written in the configuration document.

    Per-tenant pins under `tenant_overrides` are deliberately not read: this
    command writes and checks the all-tenants block, which is the one that holds
    every caller (#902). A deployment pinning per tenant is answering a different
    question than "does this file still describe what the server serves".
    """
    pins: dict[str, dict[str, str]] = {}
    for server_id, spec in (config.get("mcp_servers") or {}).items():
        if not isinstance(spec, dict):
            continue
        projection = spec.get("tool_projection")
        if not isinstance(projection, dict):
            continue
        configured = projection.get("pins")
        if isinstance(configured, dict):
            pins[str(server_id)] = {str(tool): str(digest) for tool, digest in configured.items()}
    return pins


#: The `Drift.tool` of a moved upstream: the pins' own binding changed, not one tool.
UPSTREAM_DRIFT = "(upstream)"


def configured_upstreams(config: dict[str, Any]) -> dict[str, str]:
    """The upstream identity each server's pins are bound to (`tool_projection.upstream`), where written (#1802)."""
    bound: dict[str, str] = {}
    for server_id, spec in (config.get("mcp_servers") or {}).items():
        projection = spec.get("tool_projection") if isinstance(spec, dict) else None
        upstream = projection.get("upstream") if isinstance(projection, dict) else None
        if isinstance(upstream, str) and upstream:
            bound[str(server_id)] = upstream
    return bound


def find_drift(config: dict[str, Any], observations: list[Observation]) -> list[Drift]:
    """Every disagreement between the pins in the file and the servers now.

    A tool that is served but not pinned is NOT drift: an unpinned tool is a
    deliberate state (`pins` is a subset by design), and reporting it would make
    `--check` fail for every deployment that pins some of its surface.
    """
    pins = configured_pins(config)
    groups = group_members(config)
    drift: list[Drift] = []
    for observation in observations:
        if not observation.ok or observation.mcp_server_id in groups:
            continue
        configured = pins.get(observation.mcp_server_id, {})
        bound = configured_upstreams(config).get(observation.mcp_server_id)
        if bound is not None and observation.upstream is not None and bound != observation.upstream:
            # The pins describe another upstream: none of them can be trusted for this one (#1802).
            drift.append(Drift(observation.mcp_server_id, UPSTREAM_DRIFT, bound, observation.upstream))
        for tool, expected in sorted(configured.items()):
            observed = observation.digests.get(tool)
            if observed != expected:
                drift.append(Drift(observation.mcp_server_id, tool, expected, observed))
    for group_id, member_ids in groups.items():
        drift.extend(_group_drift(config, group_id, member_ids, pins.get(group_id, {}), observations))
    return drift


def _group_drift(
    config: dict[str, Any],
    group_id: str,
    member_ids: list[str],
    configured: dict[str, str],
    observations: list[Observation],
) -> list[Drift]:
    """A group's own pins against each member that answered, as the gateway holds them.

    The gateway applies a pin declared on a group to every member (the pin
    re-check covers each member id, and the call gate compares the serving
    member's digest and upstream). So every member that serves a pinned tool
    must serve it at the pin. A member that does not serve the tool at all is
    not drift -- a call is routed to a member that has it -- but a pinned tool
    no member serves is, as for a server. A group is judged only when every
    member was asked (the group was selected, not just one of its members), and
    absence only when every member answered: one that could not be asked is
    reported unreachable already, and it may be the one serving the tool.
    """
    by_id = {o.mcp_server_id: o for o in observations}
    if not member_ids or any(m not in by_id for m in member_ids):
        return []
    if not (configured or configured_upstreams(config).get(group_id)):
        return []
    members = [by_id[m] for m in member_ids]
    answered = [o for o in members if o.ok]
    drift: list[Drift] = []
    bound = configured_upstreams(config).get(group_id)
    for member in answered:
        if bound is not None and member.upstream is not None and bound != member.upstream:
            drift.append(Drift(group_id, UPSTREAM_DRIFT, bound, member.upstream, member=member.mcp_server_id))
    for tool, expected in sorted(configured.items()):
        serving = [o for o in answered if tool in o.digests]
        for member in serving:
            if member.digests[tool] != expected:
                drift.append(Drift(group_id, tool, expected, member.digests[tool], member=member.mcp_server_id))
        if not serving and len(answered) == len(members):
            drift.append(Drift(group_id, tool, expected, None))
    return drift


def merge_pins(config: dict[str, Any], observations: list[Observation]) -> dict[str, Any]:
    """Return *config* with observed digests merged into each server's pins.

    Only servers that answered are touched, and only their `tool_projection.pins`
    key: a server that failed to start keeps whatever pin it had, because
    replacing it with nothing would silently unpin a tool the operator pinned.
    """
    merged = dict(config)
    servers = dict(merged.get("mcp_servers") or {})
    for observation in observations:
        if not observation.ok or observation.mcp_server_id not in servers:
            continue
        spec = dict(servers[observation.mcp_server_id])
        projection = dict(spec.get("tool_projection") or {})
        projection["pins"] = dict(observation.digests)
        if observation.upstream is not None:
            # Binds the pins to where they were read from: they stop holding for a
            # server of the same name that points somewhere else (#1802).
            projection["upstream"] = observation.upstream
        spec["tool_projection"] = projection
        servers[observation.mcp_server_id] = spec
    merged["mcp_servers"] = servers
    return merged


def write_config_with_pins(config_path: Path, merged: dict[str, Any]) -> Path:
    """Write *merged* back over the config file, keeping the previous one.

    PyYAML round-trips values, not comments or key order, so the rewritten file
    is semantically identical and textually normalized. That is a real cost, and
    the backup beside it is the answer chosen over adding a round-tripping YAML
    dependency for one command (see #1191). `mcp-hangar pin` with no flags
    prints the same pins to stdout for anyone who would rather paste them.
    """
    backup = config_path.with_name(config_path.name + ".bak")
    # A copy, not a rewrite, so the backup keeps the config's mode: a 0600 file's
    # previous version was written 0644 under the umask (#2048).
    shutil.copy2(config_path, backup)
    config_path.write_text(
        yaml.safe_dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return backup
