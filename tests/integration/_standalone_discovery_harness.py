"""Boot a standalone gateway on SQLite, let discovery register a server, then lose it.

Run as a script, in its own interpreter, by
``test_a_standalone_gateway_removes_a_lost_discovered_server.py``:
``python _standalone_discovery_harness.py <phase> <storage> <workdir> <out.json>``.
``phase`` is ``first`` (boot, discover, delete the definition, run past the TTL)
or ``restart`` (boot again over the same data directory). ``storage`` is
``sqlite`` (``persistence.backend: sqlite``) or ``legacy`` (no backend, and the
driver sets ``MCP_PERSISTENCE_ENABLED``). Not collected by pytest. Each phase is
its own process because a restart is one: ``bootstrap()`` fills process-global
state, and nothing may survive from the first gateway to the second except what
it stored.

What runs is production: ``bootstrap(config_path=...)``, its fleet writer and
the discovery orchestrator it builds, over a filesystem source in
``authoritative`` mode. The loops are driven by hand, as ``start()`` would run
them, so the test does not wait on their intervals.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml

SERVER = "found-one"
MOCK = Path(__file__).resolve().parents[1] / "mock_provider.py"
TTL_S = 1


def _config(storage: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "mcp_servers": {},
        "config_reload": {"enabled": False},
        "discovery": {
            "enabled": True,
            "auto_register": True,
            "security": {"require_health_check": False},
            "lifecycle": {"default_ttl_s": TTL_S},
            "sources": [{"type": "filesystem", "mode": "authoritative", "path": "sources", "watch": False}],
        },
    }
    if storage == "sqlite":
        config["persistence"] = {"backend": "sqlite", "sqlite": {"data_dir": "data"}}
    return config


def _rows(workdir: Path) -> list[str]:
    """The ids the next boot restores: a deleted row is disabled, not dropped."""
    connection = sqlite3.connect(str(workdir / "data" / "mcp_hangar.db"))
    try:
        return sorted(
            r[0] for r in connection.execute("SELECT mcp_server_id FROM mcp_server_configs WHERE enabled = 1")
        )
    finally:
        connection.close()


def run(phase: str, storage: str, workdir: Path) -> dict[str, Any]:
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.bootstrap.coordination import get_lease_keeper

    sources = workdir / "sources"
    sources.mkdir(exist_ok=True)
    definition = sources / f"{SERVER}.yaml"
    if phase == "first":
        definition.write_text(
            json.dumps(
                {
                    "name": SERVER,
                    "mode": "subprocess",
                    "ttl": TTL_S,
                    "connection": {"command": [sys.executable, str(MOCK)]},
                }
            )
        )
    path = workdir / "config.yaml"
    path.write_text(yaml.safe_dump(_config(storage)), encoding="utf-8")

    context = bootstrap(config_path=str(path))
    repository = context.runtime.repository
    orchestrator = context.discovery_orchestrator
    # Wired by start(), which would also start the background loops.
    orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
    out: dict[str, Any] = {"keeper": get_lease_keeper() is not None, "at_boot": sorted(repository.get_all_ids())}

    if phase == "first":
        asyncio.run(orchestrator.run_discovery_cycle())
        out["registered"] = sorted(repository.get_all_ids())
        out["rows_registered"] = _rows(workdir)

        definition.unlink()
        time.sleep(TTL_S + 0.5)
        asyncio.run(orchestrator.run_discovery_cycle())
        time.sleep(TTL_S + 0.5)
        out["expired"] = asyncio.run(orchestrator._lifecycle_manager._check_expirations())
        out["after_loss"] = sorted(repository.get_all_ids())

    out["rows"] = _rows(workdir)
    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(phase: str, storage: str, workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(phase, storage, workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
