"""Import the servers a client already has into the Hangar config (#1770, stage 2).

Stage 1 (`client_servers`) reads what each detected client lists. This takes
those servers, starts each once exactly as `serve` would, pins every tool it
serves while it is up, and writes the ones that answered into the Hangar config
under their own names. A server that does not answer is not written: importing a
server Hangar cannot list would put an entry behind the gateway that nobody can
see or govern (maintainer decision on #1770). It stays in the client config as
it was, and the report names it with the reason.

`env` and `headers` are copied verbatim, `${VAR}` references included. A
config that holds any `env` is left readable by its owner only (#1653).

Nothing here touches a client file; replacing the imported entries with the
single `mcp-hangar` entry is stage 3.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ...config import _interpolate_env_vars
from .client_servers import ClientServers, EntryProblem, RemoteServer, StdioServer
from .config_file import write_private
from .pinning import Observation, _observe_one, merge_pins
from .smoke_test import DEFAULT_TIMEOUT_SECONDS, MIN_PER_SERVER_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Imported:
    """A server now in the Hangar config, with its tools pinned."""

    name: str
    client_key: str
    tools: int


@dataclass(frozen=True)
class NotImported:
    """A server left where it was, and why. `reason` never quotes `env` or `headers`."""

    name: str
    client_key: str
    reason: str


@dataclass
class ImportReport:
    """What one import did, server by server."""

    imported: list[Imported] = field(default_factory=list)
    not_imported: list[NotImported] = field(default_factory=list)
    #: Entries stage 1 could not read, passed through so one summary names them all.
    problems: list[EntryProblem] = field(default_factory=list)
    #: Where the previous config was kept, or None when there was none to keep.
    backup: Path | None = None


def hangar_entry(server: StdioServer | RemoteServer) -> dict[str, Any]:
    """The Hangar `mcp_servers` entry for *server*, as the client declared it."""
    if isinstance(server, StdioServer):
        entry: dict[str, Any] = {"mode": "subprocess", "command": [server.command]}
        if server.args:
            entry["args"] = list(server.args)
        if server.env:
            entry["env"] = dict(server.env)
        return entry
    entry = {"mode": "remote", "endpoint": server.url}
    if server.headers:
        entry["http"] = {"headers": dict(server.headers)}
    return entry


Observe = Callable[[str, dict[str, Any], float], Observation]


def import_client_servers(
    config_path: Path,
    sources: Iterable[ClientServers],
    *,
    skip: frozenset[tuple[str, str]] = frozenset(),
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    observe: Observe = _observe_one,
) -> ImportReport:
    """Start, pin and write the servers of *sources* into the config at *config_path*.

    Args:
        config_path: The Hangar config. Created when it does not exist.
        sources: What stage 1 read, one entry per client file.
        skip: `(client_key, name)` pairs the user opted out of; they are reported
            as not imported and left in their client.
        timeout_s: Shared across the servers started, as `mcp-hangar pin` shares it.
        observe: Starts one server and digests its tools; a seam for tests.
    """
    report = ImportReport()
    existing = _load(config_path)
    servers: dict[str, Any] = dict(existing.get("mcp_servers") or {})

    candidates: dict[str, tuple[str, dict[str, Any]]] = {}
    for source in sources:
        report.problems.extend(source.problems)
        for server in source.servers:
            key = source.client.key
            entry = hangar_entry(server)
            reason = _refusal(server.name, key, entry, skip, servers, candidates)
            if reason is not None:
                if reason:  # "" is the same server again, from a second client: nothing to say
                    report.not_imported.append(NotImported(server.name, key, reason))
                continue
            candidates[server.name] = (key, entry)

    per_server = max(timeout_s / len(candidates), MIN_PER_SERVER_TIMEOUT_SECONDS) if candidates else timeout_s
    observations = []
    for name, (key, entry) in candidates.items():
        observation = _start(name, entry, per_server, observe)
        if observation.ok:
            servers[name] = entry
            observations.append(observation)
            report.imported.append(Imported(name, key, len(observation.digests)))
        else:
            report.not_imported.append(NotImported(name, key, f"did not start: {observation.error}"))

    if report.imported:
        merged = merge_pins({**existing, "mcp_servers": servers}, observations)
        report.backup = _write(config_path, merged)
    return report


def _refusal(
    name: str,
    client_key: str,
    entry: dict[str, Any],
    skip: frozenset[tuple[str, str]],
    configured: dict[str, Any],
    candidates: dict[str, tuple[str, dict[str, Any]]],
) -> str | None:
    """Why *name* is not imported, "" when it is a duplicate of one already taken, or None."""
    if (client_key, name) in skip:
        return "left out at your request"
    if name in configured:
        return "the Hangar config already has a server with this name"
    if name in candidates:
        return "" if candidates[name][1] == entry else f"{candidates[name][0]} lists a different server under this name"
    return None


def _start(name: str, entry: dict[str, Any], timeout_s: float, observe: Observe) -> Observation:
    """Start *entry* as `serve` would run it: `${VAR}` references resolved from this environment."""
    try:
        resolved = _interpolate_env_vars(entry)
    except Exception as exc:  # noqa: BLE001 -- a missing variable is this server's problem, not the import's
        return Observation(name, error=str(exc))
    return observe(name, resolved, timeout_s)


def _load(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    document = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{config_path} does not hold a YAML mapping")
    return document


def _write(config_path: Path, config: dict[str, Any]) -> Path | None:
    """Write *config*, keeping the previous file beside it; owner-only when it holds `env`."""
    backup = None
    if config_path.exists():
        backup = config_path.with_name(config_path.name + ".bak")
        write_private(backup, config_path.read_text(encoding="utf-8"))
    write_private(config_path, yaml.safe_dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
    if any(isinstance(spec, dict) and spec.get("env") for spec in (config.get("mcp_servers") or {}).values()):
        # `write_private` keeps the mode of a file that exists; one that now holds
        # the user's env values is tightened all the same (#1770 decision 3).
        os.chmod(config_path, 0o600)
    return backup
