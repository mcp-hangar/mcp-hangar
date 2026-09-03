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

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

import yaml

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


def observe_digests(
    config_path: Path,
    mcp_server_ids: list[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Observation]:
    """Start each configured server, read its tools, and digest them.

    Starting is the only way to ask: a tool list is what a server answers, not
    something the configuration knows. Each server is stopped again afterwards --
    this command is not a gateway.
    """
    config = load_configuration(str(config_path))
    servers: dict[str, Any] = config.get("mcp_servers", {}) or {}

    if mcp_server_ids:
        unknown = [name for name in mcp_server_ids if name not in servers]
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        servers = {name: servers[name] for name in mcp_server_ids}

    per_server_timeout = max(timeout_s / len(servers), MIN_PER_SERVER_TIMEOUT_SECONDS) if servers else timeout_s
    return [_observe_one(name, spec, per_server_timeout) for name, spec in servers.items()]


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

        digests = {
            schema.name: compute_tool_digest(schema.to_dict()).sha256 for schema in mcp_server.get_tool_schemas()
        }
        return Observation(mcp_server_id, digests=digests)
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


def find_drift(config: dict[str, Any], observations: list[Observation]) -> list[Drift]:
    """Every disagreement between the pins in the file and the servers now.

    A tool that is served but not pinned is NOT drift: an unpinned tool is a
    deliberate state (`pins` is a subset by design), and reporting it would make
    `--check` fail for every deployment that pins some of its surface.
    """
    pins = configured_pins(config)
    drift: list[Drift] = []
    for observation in observations:
        if not observation.ok:
            continue
        configured = pins.get(observation.mcp_server_id, {})
        for tool, expected in sorted(configured.items()):
            observed = observation.digests.get(tool)
            if observed != expected:
                drift.append(Drift(observation.mcp_server_id, tool, expected, observed))
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
    backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    config_path.write_text(
        yaml.safe_dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return backup
